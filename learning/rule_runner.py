"""
Rule optimiser runner — orchestrates post-session rule learning.

Includes: rule-path resolution, main `run_rule_optimizer`, legacy classifier-
gated loop, regression rollback, rule evolver (multi-source A/B), cost-signal
suggestion builder, criteria upgrader.

Extracted from `learning/runners.py` to keep each file under 500 lines.
"""
from __future__ import annotations


from core.logging import tprint
from core.config import get_bool
from core.paths import RULES_DIR, rule_path_for

from .trends import print_score_trends
from .rule_lifecycle import (
    run_legacy_rule_optimizer, rollback_regressed_rules,
    maybe_upgrade_criteria,
)



def run_rule_optimizer(orch) -> None:
    """After pipeline, auto-apply recurring rule improvements; ask user only
    for new patterns. Splits into:

      1. Record PASS patterns + score trend + checklist answers
      2. Collect REVISE reviews + chronic patterns + easy items
      3. Generate suggestions (LLM + IntegrityRules)
      4. Route through RuleEvolver (default) or legacy classifier-gated loop
      5. Regression check on past applies → rollback + blacklist
      6. Criteria upgrade when agent PASSes high consistently
      7. Cleanup old applied entries
    """
    from learning.revise_history import ReviseHistory

    history_path = RULES_DIR / orch.profile / ".revise_history.json"
    history = ReviseHistory(history_path)

    # 1) Record score + PASS patterns + checklist for each review
    for r in orch.critic_reviews:
        if r.get("score") is not None:
            history.record_score(r["agent_key"], float(r["score"]), orch.session_id)
        if r.get("verdict") == "PASS" and r.get("strengths"):
            history.record_pass(r["agent_key"], r["strengths"], orch.session_id)
        if r.get("checklist_flat") and r.get("checklist_answers"):
            history.record_checklist_answers(
                r["agent_key"], r["checklist_flat"],
                r["checklist_answers"], orch.session_id,
            )

    # 2) Collect input signals
    easy_items: list[dict] = []
    for key in set(r["agent_key"] for r in orch.critic_reviews):
        easy_items.extend(history.get_easy_items(key))

    revise_reviews = [r for r in orch.critic_reviews if r["verdict"] == "REVISE"]
    chronic_patterns = [
        e for e in history._data.values()
        if isinstance(e, dict)
        and not e.get("agent_key", "").startswith("__")
        and e.get("count", 0) >= 2
        and not e.get("applied", False)
    ]
    chronic_patterns.sort(key=lambda e: e["count"], reverse=True)

    if not revise_reviews and not chronic_patterns and not easy_items:
        tprint("\n  🧠 Rule Optimizer: không có REVISE + không có bug lặp lại — rules tốt, skip.")
        print_score_trends(orch, history)
        return

    tprint(f"\n{'─'*70}")
    label_parts = []
    if revise_reviews:
        label_parts.append(f"{len(revise_reviews)} REVISE session này")
    if chronic_patterns:
        label_parts.append(f"{len(chronic_patterns)} bug lặp lại from history")
    tprint(f"  🧠 RULE OPTIMIZER — analyze {' + '.join(label_parts)}...")
    if chronic_patterns:
        tprint(f"  📚 Chronic patterns (top 3):")
        for p in chronic_patterns[:3]:
            tprint(f"     [{p['agent_key'].upper()}] {p['count']}x — {p['reason_sample'][:70]}")
    tprint(f"{'─'*70}")

    print_score_trends(orch, history)

    if easy_items:
        tprint(f"  🟡 Easy items (luôn YES ≥5 sessions — need siết): {len(easy_items)} items")
        for ei in easy_items[:3]:
            tprint(f"     [{ei['agent_key'].upper()}] ({ei['total_count']}x) {ei['sample'][:70]}")

    # 3) LLM + Integrity suggestions
    suggestions = orch.rule_optimizer.analyze_and_suggest(
        revise_reviews, chronic_patterns, history=history, easy_items=easy_items
    )
    integrity_sugs = orch.rule_optimizer.suggest_from_integrity(
        getattr(orch, "_integrity", None)
    )
    if integrity_sugs:
        tprint(f"\n  🧬 Integrity surface {len(integrity_sugs)} deterministic "
               f"rule suggestion(s) (no LLM cost)")
        suggestions = list(suggestions or []) + integrity_sugs

    # 4) Route through RuleEvolver (default) OR legacy classifier-gated loop.
    if not get_bool("MULTI_AGENT_LEGACY_RULE_OPTIMIZER"):
        run_rule_evolver(orch, suggestions, history)
    else:
        run_legacy_rule_optimizer(orch, suggestions, history)

    # 5) Regression rollback on past applies
    rollback_regressed_rules(orch, history)

    # 6) Criteria upgrade when agent PASSes high consistently
    upgraded = maybe_upgrade_criteria(orch, history)
    if upgraded:
        tprint(f"\n  {'═'*60}")
        tprint(f"  📈 CRITERIA UPGRADED — agent liên tục đạt ≥{history.UPGRADE_AVG_THRESHOLD}"
               f" in {history.UPGRADE_MIN_SESSIONS} sessions")
        tprint(f"  {'═'*60}")
        for key, old, new in upgraded:
            tprint(f"  ⬆️  [{key.upper()}] PASS_THRESHOLD: {old} → {new}")
            tprint(f"     (backup lưu in rules/backups/)")

    # 7) Cleanup old applied entries (>30 days)
    cleaned = history.cleanup(days=30)
    if cleaned:
        tprint(f"\n  🧹 Cleaned {cleaned} old applied pattern(s) (>30 days) from history.")






def run_rule_evolver(orch, raw_suggestions: list[dict], history) -> None:
    """Product/enterprise rule evolution: provenance + multi-dim + A/B.

    Routes LLM + integrity + cost suggestions through RuleEvolver, which
    produces auto/shadow/pending lanes and can later judge shadow variants
    against baselines.
    """
    from learning.rule_evolver import RuleEvolver

    profile_dir = RULES_DIR / orch.profile
    evolver = RuleEvolver(profile_dir, session_id=orch.session_id)

    # Split incoming raw suggestions by source tag
    llm_raw:       list[dict] = []
    integrity_raw: list[dict] = []
    for s in raw_suggestions:
        if s.get("source") == "integrity":
            integrity_raw.append(s)
        else:
            llm_raw.append(s)

    cost_sugs = build_cost_suggestions(orch)
    if cost_sugs:
        tprint(f"  💰 Cost signal: {len(cost_sugs)} agent(s) over token budget")

    merged = evolver.gather(
        llm_suggestions=llm_raw,
        integrity_suggestions=integrity_raw,
        cost_suggestions=cost_sugs,
    )
    if not merged:
        tprint("  RuleEvolver: no signals → skip this session.")
    else:
        current: dict[str, str] = {}
        for s in merged:
            path = rule_path_for(orch.profile, s.agent_key, s.target_type)
            try:
                current[f"{s.agent_key}:{s.target_type}"] = (
                    path.read_text(encoding="utf-8") if path.exists() else ""
                )
            except (OSError, UnicodeDecodeError):
                current[f"{s.agent_key}:{s.target_type}"] = ""

        decided = evolver.decide(merged, current)
        filtered = []
        for s in decided:
            if history.is_blacklisted(s.agent_key, s.reason, s.target_type):
                tprint(f"  🚫 [{s.agent_key.upper()}] Skip — blacklisted regression pattern.")
                continue
            conflict = history.conflicts_with_pass_patterns(s.agent_key, s.addition)
            if conflict:
                tprint(f"  🛡️  [{s.agent_key.upper()}] Skip — conflicts PASS pattern.")
                continue
            filtered.append(s)

        # RuleEvolver.apply expects a resolver taking (agent_key, target_type).
        # Bind orch.profile so we reuse our central helper.
        def _resolver(agent_key: str, target_type: str):
            return rule_path_for(orch.profile, agent_key, target_type)

        result = evolver.apply(filtered, _resolver)

        tprint(f"\n  {'═'*60}")
        tprint(f"  🧬 RULE EVOLVER")
        tprint(f"  {'═'*60}")
        tprint(f"  ✅ auto-applied : {len(result['applied'])}")
        tprint(f"  🧪 shadowed A/B : {len(result['shadowed'])}")
        tprint(f"  ⏳ pending     : {len(result['pending'])}")
        for item in result["applied"][:5]:
            src = "+".join(item["sources"])
            tprint(f"     ✅ [{item['agent_key'].upper()}] "
                   f"src={src} score={item['multi_dim']:.2f} — "
                   f"{item['reason'][:70]}")
        for item in result["shadowed"][:5]:
            tprint(f"     🧪 [{item['agent_key'].upper()}] shadow — "
                   f"{item['reason'][:70]}")

    # Evaluate live shadows (promote/demote based on accumulated scores).
    def _resolver2(agent_key: str, target_type: str):
        return rule_path_for(orch.profile, agent_key, target_type)

    actions  = evolver.evaluate_shadows(_resolver2)
    promoted = [a for a in actions if a["action"] == "promote"]
    demoted  = [a for a in actions if a["action"] == "demote"]
    if promoted or demoted:
        tprint(f"\n  🔬 Shadow verdicts: "
               f"{len(promoted)} promoted, {len(demoted)} demoted")
        for a in promoted:
            tprint(f"     ⬆️  PROMOTE {a['key']} (+{a['delta']:.2f})")
        for a in demoted:
            tprint(f"     ⬇️  DEMOTE  {a['key']} ({a['delta']:.2f})")


def build_cost_suggestions(orch) -> list:
    """Emit cost-driven rule suggestions ONLY when an agent is reliably over
    budget across a trend window.

    Triggers require BOTH:
      (a) Current-session ratio ≥ COST_RATIO_OVER_BUDGET (1.5×)
      (b) ≥ COST_TREND_REQUIRED_OVER sessions in the last COST_TREND_WINDOW
          were also over the ratio threshold.

    Expected budget per agent comes from task metadata (scope × complexity),
    not a flat constant — legitimate XL work isn't flagged as over-budget.
    """
    from learning.rule_evolver import Suggestion, SRC_COST
    from analyzer.cost_history import (
        COST_FALLBACK, COST_RATIO_OVER_BUDGET,
        COST_TREND_WINDOW, COST_TREND_REQUIRED_OVER,
        load_cost_history, save_cost_history,
        load_cost_budgets, expected_budget_for_tasks,
    )

    tracker = getattr(orch, "tokens", None)
    if tracker is None or not tracker.records:
        return []

    role_to_key = {
        "Business Analyst (BA)": "ba",
        "UI/UX Designer":        "design",
        "Tech Lead":             "techlead",
        "Developer":             "dev",
        "QA/Tester":             "test",
        "Project Manager (PM)":  "pm",
    }
    actual: dict[str, int] = {}
    for rec in tracker.records:
        key = role_to_key.get(rec.agent, rec.agent.lower())
        actual[key] = actual.get(key, 0) + rec.total

    tasks    = getattr(orch, "_current_tasks_for_cost", None) or []
    budgets  = load_cost_budgets(orch.profile)
    expected = expected_budget_for_tasks(tasks, budgets)
    history  = load_cost_history(orch.profile)

    out: list = []
    for agent_key, spent in actual.items():
        exp = expected.get(agent_key, COST_FALLBACK)
        if exp <= 0:
            continue
        ratio = spent / exp
        ratios = history.setdefault(agent_key, [])
        ratios.append(round(ratio, 3))
        if len(ratios) > COST_TREND_WINDOW * 2:
            history[agent_key] = ratios[-COST_TREND_WINDOW * 2:]

        if ratio < COST_RATIO_OVER_BUDGET:
            continue
        recent = ratios[-COST_TREND_WINDOW:]
        n_over = sum(1 for r in recent if r >= COST_RATIO_OVER_BUDGET)
        if n_over < COST_TREND_REQUIRED_OVER:
            continue

        addition = (
            f"- Cost trend: `{agent_key}` ran at {ratio * 100:.0f}% of "
            f"expected budget this session ({spent:,} / {exp:,} tokens). "
            f"Over budget in {n_over}/{len(recent)} of recent sessions. "
            "Trim output to essentials — omit boilerplate, summarise "
            "instead of restating, cut examples to one canonical case."
        )
        out.append(Suggestion(
            agent_key=agent_key, target_type="rule",
            addition=addition,
            reason=(f"Cost trend {n_over}/{len(recent)} over "
                    f"{COST_RATIO_OVER_BUDGET:.1f}× budget"),
            sources=[SRC_COST],
            session_id=orch.session_id,
            score_cost=0.9,
            score_correctness=0.55,
        ))

    save_cost_history(orch.profile, history)
    return out




