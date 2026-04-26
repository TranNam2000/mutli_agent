"""
PM routing helpers — classify a request into a kind, pick the dispatch
plan, and stamp PM-authoritative metadata onto tasks.

Extracted from orchestrator so the routing logic is testable without the
full pipeline machinery.

Public API
----------
    run_pm_router         (orch, product_idea) -> RouteDecision
    run_investigation_path(orch, product_idea) -> dict
    apply_pm_metadata     (orch, route, tasks) -> int
"""
from __future__ import annotations

import re

from core.logging import tprint
from core.config import get_bool
from agents.pm_agent import PMAgent, ALL_KINDS as PM_ALL_KINDS, RouteDecision


def _confirm_plan_with_user(decision: RouteDecision, product_idea: str,
                              orch) -> RouteDecision:
    """Show user the PM's understanding + planned dispatch, ask to confirm
    before spending LLM calls on BA/Design/TL/Dev/Test.

    User options:
      [Enter / Y]  — proceed as-is
      [n]          — abort the run (user rethinks)
      [e]          — edit: user types a refined request, PM re-classifies
      [s]          — show: print the full PM markdown reason

    Skipped entirely when MULTI_AGENT_PM_AUTO_CONFIRM=1 (CI / power-user
    mode). Also skipped on resumed sessions (decision.source=='checkpoint')
    to avoid re-prompting the same plan.
    """
    if get_bool("MULTI_AGENT_PM_AUTO_CONFIRM"):
        return decision
    if decision.source == "checkpoint":
        return decision

    pm: PMAgent = orch.agents["pm"]
    skill_key = "(default)"
    if pm._active_skills:
        skill_key = pm._active_skills[0].get("skill_key", "(unnamed)")
    steps = decision.dispatch_steps()

    while True:
        tprint("\n" + "─" * 64)
        tprint("  🧭 PM PLAN — please confirm before dispatching agents")
        tprint("─" * 64)
        tprint(f"     Request   : {product_idea[:80].strip()}{'...' if len(product_idea)>80 else ''}")
        tprint(f"     Kind      : {decision.kind}")
        tprint(f"     Skill     : {skill_key}")
        tprint(f"     Confidence: {decision.confidence:.2f}")
        tprint(f"     Reason    : {decision.reason[:140]}")
        tprint(f"     Will run  : {' → '.join(steps) if steps else '(no agents — clarification only)'}")
        tprint("")
        choice = input(
            "     Proceed? [Enter / Y]es  [n]o  [e]dit request  [s]how full plan: "
        ).strip().lower()

        if choice in ("", "y", "yes"):
            return decision
        if choice in ("n", "no"):
            tprint("  ⏹  PM plan rejected — aborting run.")
            decision.kind = "aborted"
            decision.dynamic_steps = []
            return decision
        if choice == "s":
            tprint("\n" + decision.to_markdown())
            continue
        if choice == "e":
            refined = input("     New / refined request: ").strip()
            if not refined:
                continue
            tprint("  🔄 Re-classifying with refined request...")
            decision = pm.classify(refined)
            product_idea = refined  # so next loop's display reflects refinement
            steps = decision.dispatch_steps()
            if pm._active_skills:
                skill_key = pm._active_skills[0].get("skill_key", "(unnamed)")
            continue
        tprint("     ↪ Unrecognised — try again.")


def run_pm_router(orch, product_idea: str) -> RouteDecision:
    """
    Classify the request and decide which sub-pipeline to run.

    Returns a RouteDecision. The caller is expected to consult
    decision.dispatch_steps() to know which agents to run.

    Honors checkpoints — if pm.md already exists for this session, parse it
    back into a RouteDecision and skip the LLM call.
    """
    pm: PMAgent = orch.agents["pm"]

    # Resume from checkpoint if present.
    if orch._step_done("pm"):
        cached = orch.results.get("pm", "")
        parsed_kind = None
        parsed_conf = 0.85  # assume previous run had good confidence
        m = re.search(r"\*\*Kind\*\*:\s*`([a-z_]+)`", cached)
        if m and m.group(1) in PM_ALL_KINDS:
            parsed_kind = m.group(1)
        if parsed_kind:
            orch._skip("pm", "PM Router")
            return RouteDecision(
                kind=parsed_kind,
                confidence=parsed_conf,
                reason="(restored from checkpoint)",
                source="checkpoint",
            )
        # Fall through to re-run if checkpoint was malformed.

    if not orch._check_quota("PM routing"):
        # Quota blown — default to feature to preserve legacy behavior.
        return RouteDecision(
            kind="feature", confidence=0.5,
            reason="PM skipped due to token quota.", source="default",
        )

    orch._header(0, len(orch.PIPELINE), "PM Router", "classifying request...")
    pm._current_step = "pm"
    try:
        pm.detect_skill(product_idea)
    except (ValueError, KeyError, AttributeError, TypeError):
        pass
    decision = pm.classify(product_idea)

    # Low-confidence path → ask user to confirm the kind.
    if decision.confidence < 0.6:
        decision = orch._pm_clarify_with_user(decision, product_idea)

    # User-confirmation gate: ALWAYS show the PM's understanding + planned
    # dispatch before spending LLM calls on BA/Design/TL/Dev/Test. Bypass
    # via MULTI_AGENT_PM_AUTO_CONFIRM=1 for CI / non-interactive runs.
    decision = _confirm_plan_with_user(decision, product_idea, orch)
    if decision.kind == "aborted":
        return decision

    tprint(f"\n  🧭 PM routed → kind=`{decision.kind}` "
           f"(confidence={decision.confidence:.2f}, via {decision.source})")
    tprint(f"     Reason: {decision.reason[:120]}")
    tprint(f"     Dispatch: {' → '.join(decision.dispatch_steps())}")

    orch._save("pm", decision.to_markdown())
    orch.results["pm"] = decision.to_markdown()
    orch._step_token_status("PM")
    return decision


def run_investigation_path(orch, product_idea: str) -> dict:
    """Sub-pipeline for kind=investigation — skip BA/Design/TL/Dev/Test."""
    orch._header(1, 1, "Code Investigator", "answering request via investigation only...")
    if not orch._check_quota("Investigation"):
        return {}
    try:
        if not orch.investigator.project_context:
            # Investigation without a project context still runs, but degrades to Q&A.
            tprint("  ℹ️  No project context loaded — running Q&A mode.")
        report = orch.investigator.investigate(product_idea)
    except Exception as e:
        tprint(f"  ❌ Investigation failed: {e}")
        return {}

    if report:
        orch.investigator.print_report(report)
        orch._save("investigation", report)
        orch.results["investigation"] = report
    orch._step_token_status("Investigation")
    return orch.results


def apply_pm_metadata(orch, route, tasks: list) -> int:
    """
    PM is the **single source of truth** for `scope`. After BA parses
    tasks, we overwrite `metadata.context.scope` to match PM's kind so
    downstream gates, audit log and analytics always agree with PM.

    If PM decomposed the request into sub_tasks, we match each sub_task
    description against task titles (case-insensitive substring) and
    apply that sub_task's kind specifically; tasks with no match inherit
    the top-level kind.

    Returns: number of tasks whose metadata was overwritten.
    """
    if route is None or not tasks:
        return 0
    default_scope = orch._PM_KIND_TO_SCOPE.get(route.kind, route.kind)
    sub_tasks = list(getattr(route, "sub_tasks", []) or [])

    def _match_sub(task) -> str | None:
        """Try to map this task to one of PM's sub_tasks via title match."""
        if not sub_tasks:
            return None
        hay = (task.title or "").lower() + " " + (task.description or "").lower()
        for st in sub_tasks:
            desc = (st.get("desc") or "").lower()
            if desc and desc[:30] in hay:
                return orch._PM_KIND_TO_SCOPE.get(st.get("kind"), st.get("kind"))
        return None

    changed = 0
    for t in tasks:
        m = t.get_metadata() if hasattr(t, "get_metadata") else None
        if m is None:
            continue
        chosen = _match_sub(t) or default_scope
        if m.context.scope != chosen:
            m.context.scope = chosen
            changed += 1
    return changed
