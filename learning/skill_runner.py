"""
Skill optimiser runner — CREATE / REFINE / MERGE / Shadow A/B / Deprecate.

Extracted from `learning/runners.py` so skill learning lives near the other
skill helpers and stays below the 500-line soft limit.
"""
from __future__ import annotations

from pathlib import Path

from core.logging import tprint
from core.config import get_bool
from core.paths import SKILLS_DIR


def run_skill_optimizer(orch) -> None:
    """Meta-learn at skill granularity: CREATE / REFINE / MERGE / Shadow / Deprecate."""
    try:
        from learning.skill_optimizer import SkillOptimizer
    except ImportError:
        return

    opt = SkillOptimizer(profile=orch.profile)
    skill_usage: list[dict] = []
    for agent in list(orch.agents.values()) + [orch.critic, orch.investigator]:
        skill_usage.extend(getattr(agent, "_skill_usage_log", []) or [])

    opt.record_from_critic_reviews(orch.critic_reviews, skill_usage, orch.session_id)
    opt.print_stats()

    # 1️⃣ Judge shadow skills first (A/B decisions)
    judge_shadow_skills(orch, opt)

    # 2️⃣ Deprecate chronically underperforming skills
    deprecated = opt.deprecate_underperforming()
    if deprecated:
        tprint(f"\n  ❌ Skills deprecated (avg < 5.0 across ≥5 uses):")
        for agent_key, skill, avg in deprecated:
            tprint(f"     {agent_key}/{skill} (avg={avg:.1f}) → .md.deprecated")

    # 3️⃣ Suggest REFINE for mid-score skills
    refinements = opt.suggest_refinements()
    for r in refinements[:2]:
        apply_refine(orch, opt, r)

    # 4️⃣ Suggest NEW skills for chronic misfit
    creations = opt.suggest_new_skills()
    for s in creations[:2]:
        apply_create(orch, opt, s)

    # 5️⃣ Suggest MERGE for near-duplicate skills
    merges = opt.suggest_merges()
    for m in merges[:1]:
        apply_merge(orch, opt, m)


def judge_shadow_skills(orch, optimizer) -> None:
    """A/B verdict on shadow skills that accumulated enough uses."""
    ready = optimizer.history.shadows_ready_to_judge()
    if not ready:
        return
    tprint(f"\n  🔬 Shadow A/B judge — {len(ready)} skill(s) đủ dữ liệu để quyết định:")
    for r in ready:
        shadow = r["shadow_entry"]
        delta  = r["delta"]
        icon   = "✅" if delta >= 0.5 else ("≈" if delta >= -0.3 else "❌")
        tprint(f"     {icon} {shadow['agent_key']}/{shadow['skill_key']} "
               f"shadow={r['shadow_avg']:.1f}  parent={r['parent_avg']:.1f}  Δ={delta:+.1f}")
        if delta >= 0.5:
            optimizer.promote_shadow(shadow)
            tprint(f"       → PROMOTED: shadow thay thế parent")
        else:
            optimizer.demote_shadow(shadow)
            tprint(f"       → REJECTED: shadow bị rollback, giữ parent")


def apply_refine(orch, optimizer, suggestion: dict) -> None:
    agent_key = suggestion["agent_key"]
    skill_key = suggestion["skill_key"]
    avg       = suggestion["avg_score"]

    skill_path = SKILLS_DIR / agent_key / f"{skill_key}.md"
    if not skill_path.exists():
        return
    current_content = skill_path.read_text(encoding="utf-8")

    # Weaknesses for this agent in this session
    weaknesses: list[str] = []
    for r in orch.critic_reviews:
        if r["agent_key"] == agent_key and r.get("weaknesses"):
            weaknesses.extend(r["weaknesses"][:3])

    tprint(f"\n  🔧 REFINE candidate: {agent_key}/{skill_key} avg={avg:.1f}")
    if weaknesses:
        tprint(f"     Weaknesses:")
        for w in weaknesses[:3]:
            tprint(f"       • {w[:90]}")

    orch.skill_designer._current_step = "skill_refine"
    try:
        result = orch.skill_designer.refine_existing(
            agent_key, skill_key, current_content, weaknesses, avg,
        )
    except Exception as e:
        tprint(f"     ❌ Refine failed: {type(e).__name__}: {e}")
        return

    if not result["ok"]:
        tprint(f"     ⏭  Abort: {result.get('reason', 'no reason')}")
        return
    if result["confidence"] == "LOW":
        tprint(f"     ⏭  Confidence LOW — skip ({result.get('rationale', '')})")
        return

    new_path, backup = optimizer.refine_skill(agent_key, skill_key, result["content"])
    tprint(f"     🆕 Shadow written → {new_path.name}  (backup: {Path(backup).name})")
    tprint(f"     ℹ️  Will A/B test qua 2 session tới trước khi promote.")


def apply_create(orch, optimizer, suggestion: dict) -> None:
    """Auto-create a new skill for chronic misfit patterns (gated)."""
    agent_key = suggestion["agent_key"]
    proposed  = suggestion["proposed_skill_key"]
    pattern   = suggestion["pattern"]
    task_smp  = suggestion["task_sample"]

    tprint(f"\n  💡 NEW SKILL auto-create: [{agent_key.upper()}] → {proposed}")
    tprint(f"     Pattern recurred {suggestion['count']} sessions: {pattern[:90]}")

    orch.skill_designer._current_step = "skill_create"
    try:
        result = orch.skill_designer.design_new_skill(agent_key, proposed, pattern, task_smp)
    except Exception as e:
        tprint(f"     ❌ Design failed: {type(e).__name__}: {e}")
        return

    if not result["ok"]:
        tprint(f"     ⏭  SkillDesigner aborted: {result.get('reason', 'no reason')}")
        return
    if result["confidence"] == "LOW":
        tprint(f"     ⏭  Confidence LOW — skip (tránh bloat skill list)")
        tprint(f"        Rationale: {result.get('rationale', '-')}")
        return

    if get_bool("MULTI_AGENT_SKILL_REVIEW"):
        tprint(f"     {'─'*60}")
        for line in result["content"].splitlines()[:25]:
            tprint(f"     {line}")
        if result["content"].count("\n") > 25:
            tprint(f"     ... (+{result['content'].count(chr(10)) - 25} more lines)")
        tprint(f"     Confidence: {result['confidence']} — {result.get('rationale', '')}")
        tprint(f"     {'─'*60}")
        confirm = input(f"     Ghi file? [Y/n] ").strip().lower()
        if confirm == "n":
            tprint(f"     ⏭  User rejected.")
            return

    path = optimizer.write_new_skill(agent_key, proposed, result["content"], shadow_for=None)
    optimizer.history.mark_status(agent_key, proposed, "shadow")
    tprint(f"     ✅ Shadow skill → {path.name}")
    tprint(f"     ⚖️  A/B judge: will quyết định sau 2 uses more")


def apply_merge(orch, optimizer, suggestion: dict) -> None:
    """Auto-merge near-duplicate skills (trigger overlap ≥ 70%)."""
    agent_key = suggestion["agent_key"]
    a = suggestion["skill_a"]; b = suggestion["skill_b"]
    tprint(f"\n  🔗 MERGE auto-applied: {agent_key}  {a} ↔ {b}  "
           f"overlap={suggestion['overlap']:.0%}")

    skills_dir = SKILLS_DIR / agent_key
    a_path = skills_dir / f"{a}.md"
    b_path = skills_dir / f"{b}.md"
    if not (a_path.exists() and b_path.exists()):
        return

    try:
        result = orch.skill_designer.design_merge(
            agent_key,
            a_path.read_text(encoding="utf-8"),
            b_path.read_text(encoding="utf-8"),
        )
    except Exception as e:
        tprint(f"     ❌ Merge design failed: {type(e).__name__}: {e}")
        return
    if not result["ok"]:
        tprint(f"     ⏭  SkillDesigner aborted: {result.get('reason', '')}")
        return

    if get_bool("MULTI_AGENT_SKILL_REVIEW"):
        tprint(f"     {'─'*60}")
        for line in result["content"].splitlines()[:20]:
            tprint(f"     {line}")
        tprint(f"     {'─'*60}")
        confirm = input(f"     Apply merge? [Y/n] ").strip().lower()
        if confirm == "n":
            tprint(f"     ⏭  User rejected.")
            return

    merged_key = optimizer._unique_key(agent_key, f"{a}_merged")
    optimizer.write_new_skill(agent_key, merged_key, result["content"], shadow_for=a)
    b_path.rename(b_path.with_suffix(".merged.md"))
    optimizer.history.mark_status(agent_key, b, "retired_merged")
    tprint(f"     🧩 Merged → {merged_key}.md (shadow). {b}.md → .merged.md")


