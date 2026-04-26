"""
Critic gating + emergency audit — decides when to run Critic, when to skip,
and when to force-audit after a downstream QA failure exposes that we let
bad upstream output through.

Extracted from orchestrator so the gate decisions are testable in isolation
with stub agents and stub AuditLog.

Public API
----------
    apply_dynamic_weights     (orch, review, agent) -> review
    techlead_touches_core     (orch, tl_output) -> (bool, matches)
    get_audit_log             (orch) -> AuditLog
    record_critic_skip        (orch, key, tasks) -> None
    trigger_emergency_audit   (orch, blockers, tasks, agent_in_charge) -> None
    fast_track_announce       (orch, key, context) -> None
    critic_enabled_for        (orch, key, output, context) -> bool
"""
from __future__ import annotations

import re

from core.logging import tprint
from core.config import get_bool


def apply_dynamic_weights(orch, review: dict, agent):
    """Recompute review score using scope-aware weights from the active skill."""
    try:
        from analyzer.score_adjuster import ScoreAdjuster
    except ImportError:
        return review
    active = getattr(agent, "_active_skill", None)
    scope  = (active or {}).get("detected_scope")
    if not scope:
        return review
    adj = ScoreAdjuster()
    new_review = adj.recompute_with_scope(review, scope)
    if new_review.get("score_before_scope_reweight") is not None:
        old = new_review["score_before_scope_reweight"]
        new_final = new_review["score"]
        if old != new_final:
            tprint(f"  ⚖️  [{agent.ROLE}] score re-weighted by scope={scope}: {old} → {new_final}/10")
    return new_review


def techlead_touches_core(orch, tl_output: str) -> tuple[bool, list[str]]:
    """Return (touches, matched_patterns). Used to gate Critic for TL."""
    if not tl_output:
        return False, []
    matched: list[str] = []
    for pat in orch._CORE_FILE_PATTERNS:
        m = re.search(pat, tl_output, re.IGNORECASE)
        if m:
            matched.append(m.group(0))
    return (len(matched) > 0, matched)


def get_audit_log(orch):
    """Lazily construct the session-level AuditLog."""
    if orch._audit_log is None:
        from learning.audit_log import AuditLog
        from core.paths import RULES_DIR as _RULES_DIR
        session_dir = orch._checkpoint_path("ba").parent
        profile_dir = _RULES_DIR / orch.profile
        orch._audit_log = AuditLog(session_dir, profile_dir)
    return orch._audit_log


def record_critic_skip(orch, key: str, tasks: list) -> None:
    """Remember which roles we skipped for each task — needed for RCA later."""
    role = orch._KEY_TO_ROLE.get(key, key.upper())
    for t in tasks:
        tid = getattr(t, "id", None)
        if not tid:
            continue
        orch._skipped_critic_by_task.setdefault(tid, []).append(role)
        m = t.get_metadata() if hasattr(t, "get_metadata") else None
        if m and tid not in orch._skip_snapshot:
            orch._skip_snapshot[tid] = m.to_dict()


def trigger_emergency_audit(orch, blockers: list[str], tasks: list,
                              agent_in_charge: str = "Dev") -> list[dict]:
    """Activate Emergency Audit Mode: record false-negatives + force Critic.

    Called the first time QA surfaces BLOCKERs. Returns list of audit
    entries written so callers can render a human-friendly summary.
    """
    if orch._emergency_audit:
        # Already active — still log, but don't re-announce.
        pass
    else:
        tprint(f"\n  {'🚨'*3}  EMERGENCY AUDIT MODE  {'🚨'*3}")
        tprint(f"     QA found {len(blockers)} BLOCKER(s) on tasks that had "
               f"Critic skipped — activating full audit.")
        tprint(f"     Critic will be FORCED ON for the remainder of this session.")
        tprint(f"  {'─'*60}")
        orch._emergency_audit = True

    # Only log false-negatives for tasks where we actually skipped some role.
    entries: list[dict] = []
    if not tasks:
        return entries
    from learning.audit_log import classify_outcome, make_root_cause_hint
    outcome = classify_outcome(blockers)
    audit = get_audit_log(orch)
    for t in tasks:
        tid = getattr(t, "id", None)
        if not tid:
            continue
        skipped_roles = orch._skipped_critic_by_task.get(tid, [])
        if not skipped_roles:
            continue   # no skip happened → not a false-negative
        meta = orch._skip_snapshot.get(tid, {})
        hint = make_root_cause_hint(meta, skipped_roles, blockers)
        e = audit.record(
            session_id=orch.session_id,
            task_id=tid,
            predicted_metadata=meta,
            skipped_for_roles=skipped_roles,
            actual_outcome=outcome,
            blockers=blockers,
            agent_in_charge=agent_in_charge,
            root_cause_hint=hint,
        )
        entries.append(e)
        tprint(f"     📼 RCA logged for {tid}: {hint}")

        # Mutate metadata in memory so downstream (revise loop, RuleOptimizer)
        # sees the upgraded risk.
        m = t.get_metadata() if hasattr(t, "get_metadata") else None
        if m is not None:
            m.context.risk_level = "high"
            # Clear skip_critic list so future runs respect the upgrade.
            m.flow_control.skip_critic = []

        # Feed the failure into IntegrityRules so future sessions learn.
        if getattr(orch, "_integrity", None) is not None:
            change = orch._integrity.record_failure(
                module=getattr(t, "module", ""),
                impact_areas=(m.technical_debt.impact_area if m else []),
                agent_in_charge=agent_in_charge,
                skipped_roles=skipped_roles,
                blockers=blockers,
            )
            bumped = ", ".join(
                f"{b['module']}({b['count']})" for b in change.get("modules_bumped", [])
            )
            if bumped:
                tprint(f"     🏷  Module counter bumped: {bumped}")
            for fw in change.get("new_forced_windows", []):
                tprint(f"     🔒 {fw['role']} entered forced-Critic "
                       f"window ({fw['window']} tasks)")
            for kw in change.get("keywords_promoted", []):
                tprint(f"     🆙 Keyword risk: '{kw['keyword']}' → "
                       f"{kw['risk']}")

    # Regenerate the human-readable integrity.md artefact (the demo file).
    if entries and getattr(orch, "_integrity", None) is not None:
        from core.paths import RULES_DIR as _RULES_DIR
        path = orch._integrity.write_integrity_rules_md(_RULES_DIR / orch.profile)
        tprint(f"     📜 integrity.md updated → {path}")

    return entries


def fast_track_announce(orch, key: str, context: dict) -> None:
    """Print a human-readable 'Fast-Track' message explaining why Critic
    was skipped for this step. Uses metadata in context when available."""
    tasks = context.get("tasks") or []
    reason = "low-risk step"
    if tasks:
        if any(t.get_metadata().is_hot_p0() for t in tasks):
            reason = "hotfix P0 — skipping intermediate Critic for speed"
        elif all(t.get_metadata().is_low_risk_small() for t in tasks):
            reason = "all tasks Low-risk + S complexity → Fast-Track mode"
        elif key == "techlead":
            reason = "standard op (no core-file touches, no L/XL) → straight to Dev"

    # Big marquee message for the "Fast-Track" narrative.
    tprint(f"  🚀 Fast-Track: Critic skipped for [{key}] ({reason}) "
           f"— saving ~1 LLM call")


def critic_enabled_for(orch, key: str, output: str = "",
                         context: dict | None = None) -> bool:
    """True iff Critic should run for this step key.

    Decision order (highest precedence first):
      1. MULTI_AGENT_CRITIC_ALL=1 → always run (legacy).
      2. Role-specific env override (MULTI_AGENT_TL_CRITIC_ALWAYS/NEVER).
      3. Metadata-driven rules derived from the tasks in `context["tasks"]`:
         3a. If ANY task is `hotfix+P0` → skip PM/BA/TL, keep Dev/QA.
         3b. If ANY task.touches_core() → force Critic (payment/auth/core).
         3c. If ALL tasks are S+low → skip PM/BA/TL.
         3d. Per-task `flow_control.skip_critic` inclusion of this role.
      4. TechLead secondary rule (unchanged): any L/XL or bug/hotfix type.
      5. Fallback: key in CRITIC_STEPS (dev, test).

    `context` may contain:
      {"tasks": [Task, Task, ...]}   # preferred (metadata-driven)
      {"tl_complexities": ["S","M"], "tl_types": ["logic","bug"]}  # legacy
    """
    if get_bool("MULTI_AGENT_CRITIC_ALL"):
        return True
    # Emergency Audit Mode — a false-negative has already bitten us in
    # this session, so the whole pipeline is demoted to "Critic everywhere"
    # until the session ends.
    if getattr(orch, "_emergency_audit", False):
        return True

    # Integrity rules — learned from past sessions.
    integrity = getattr(orch, "_integrity", None)
    role = orch._KEY_TO_ROLE.get(key, key.upper())

    # (a) Forced-Critic window for a role whose reputation is bad.
    if integrity is not None and integrity.role_has_forced_window(role):
        tprint(f"  🛡  Forced-Critic window active for {role} — running Critic")
        integrity.consume_forced_window(role)
        return True

    # (b) Module blacklist: any task's impact_area matches → force Critic.
    if integrity is not None:
        tasks_ctx = (context or {}).get("tasks") or []
        for t in tasks_ctx:
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
            if not m:
                continue
            areas = list(m.technical_debt.impact_area) + [
                getattr(t, "module", "") or ""
            ]
            if any(integrity.module_forces_critic(a) for a in areas if a):
                tprint(f"  🛡  Module in integrity blacklist → running Critic")
                return True

    # TechLead env overrides kept for backward compat.
    if key == "techlead":
        if get_bool("MULTI_AGENT_TL_CRITIC_NEVER"):
            return False
        if get_bool("MULTI_AGENT_TL_CRITIC_ALWAYS"):
            return True

    role = orch._KEY_TO_ROLE.get(key, key.upper())
    ctx = context or {}
    tasks = ctx.get("tasks") or []

    # Backward-compat: if caller only passed tl_complexities/tl_types but
    # no full Task list, synthesise a lightweight context.
    complexities: list[str] = [str(c).upper() for c in ctx.get("tl_complexities", [])]
    types:        list[str] = [str(t).lower() for t in ctx.get("tl_types", [])]
    for t in tasks:
        m = t.get_metadata() if hasattr(t, "get_metadata") else None
        if m:
            complexities.append(m.context.complexity)
            types.append(m.context.scope)

    # ── Metadata-driven rules ──
    if tasks:
        any_hot_p0       = any(t.get_metadata().is_hot_p0()       for t in tasks)
        any_touches_core = any(t.get_metadata().touches_core()    for t in tasks)
        all_low_small    = all(t.get_metadata().is_low_risk_small() for t in tasks)

        # 3a. Hotfix P0 → skip PM/BA/TL, keep Dev+QA regardless.
        if any_hot_p0 and key in ("pm", "ba", "techlead", "design", "test_plan"):
            return False

        # 3b. Core-touch task → force Critic on TL (and Dev/Test are already on).
        if any_touches_core and key == "techlead":
            return True

        # 3c. All tasks low-risk + S complexity → skip PM/BA/TL bundle.
        if all_low_small and key in ("pm", "ba", "techlead"):
            return False

        # 3d. Respect per-task skip list: if every task in scope has skipped
        # this role, honour it.
        if all(role in t.get_metadata().flow_control.skip_critic for t in tasks):
            return False

    # ── TechLead secondary rules (unchanged) ──
    if key == "techlead":
        if any(c in ("L", "XL") for c in complexities):
            return True
        if any(t in ("bug", "bug_fix", "hotfix") for t in types):
            return True
        if complexities and all(c in ("S", "M") for c in complexities):
            return False
        touches, _ = techlead_touches_core(orch, output)
        return touches

    return key in orch.CRITIC_STEPS
