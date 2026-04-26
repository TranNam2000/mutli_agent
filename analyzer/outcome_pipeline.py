"""
Outcome analysis pipeline — the bridge between a finished session and the
analyzer modules that record / score / predict.

Extracted from `orchestrator._apply_outcome_adjustments` so the orchestrator
stays thin and this logic is unit-testable with synthetic reviews + bus.

Flow
----
    for each finished session:
      1. ScoreAdjuster.recompute_with_scope   (scope reweight)
      2. ScoreAdjuster.apply_test_outcomes    (Patrol/Maestro, with attribution)
      3. ScoreAdjuster.apply_downstream_signals (clarifications + MISSING_INFO)
      4. ScoreAdjuster.apply_cost_penalty     (scope budget)
      5. outcome_logger.log_session_outcomes  (per-agent JSONL)
      6. skill_outcome_logger.log_session_skills (per-(agent, skill) JSONL)
      7. score_renderer.print_score_breakdown  (terminal output)

All side-effect functions are passed in / injected so orchestrator wiring
stays explicit.
"""
from __future__ import annotations

from typing import Any

from core.logging import tprint


# Role → agent_key used by most of the downstream helpers.
_ROLE_TO_KEY = {
    "Business Analyst (BA)": "ba",
    "Tech Lead":             "techlead",
    "UI/UX Designer":        "design",
    "Developer":             "dev",
    "QA/Tester":             "test",
    "Project Manager (PM)":  "pm",
}


def _patrol_test_pass_rate(patrol_result: Any) -> float | None:
    """Aggregate android + ios pass rates from a PatrolSuiteResult."""
    if not patrol_result:
        return None
    rates: list[float] = []
    for side_name in ("android", "ios"):
        side = getattr(patrol_result, side_name, None)
        if side:
            total = side.passed + side.failed
            if total:
                rates.append(side.passed / total)
    return sum(rates) / len(rates) if rates else None


def analyze_session(
    *,
    profile: str,
    session_id: str,
    task: str,
    critic_reviews: list[dict],
    bus,
    tokens,
    agents: dict[str, Any],
    current_tasks: list | None = None,
    patrol_result: Any | None = None,
    maestro_result: Any | None = None,
) -> dict:
    """
    Run the full outcome-analysis pipeline for one finished session.

    Returns a small summary dict (mostly for tests); all interesting output
    is either persisted to disk (JSONL) or printed via `tprint`.
    """
    if not critic_reviews:
        return {"adjustments": 0, "logged": 0, "logged_skills": 0}

    from analyzer import (
        ScoreAdjuster, count_clarifications_from_bus, count_missing_info,
        log_session_outcomes, log_session_skills, print_score_breakdown,
    )
    from analyzer.cost_history import load_cost_budgets, expected_budget_for_tasks
    from pipeline.skill_selector import detect_scope

    adj = ScoreAdjuster()

    # 1) Test-informed, with MISSING_INFO attribution so Dev isn't punished
    # when the failure is BA's fault.
    dev_output = ""
    for r in critic_reviews:
        if r.get("agent_key") == "dev":
            dev_output = r.get("output", "")
            break
    # Orchestrator stores dev output in results; fall back to empty.
    missing_by_agent = count_missing_info(dev_output)

    adj.apply_test_outcomes(
        critic_reviews,
        patrol_result=patrol_result,
        maestro_result=maestro_result,
        missing_info_attribution=missing_by_agent,
    )

    # 2) Downstream signals (clarifications + MISSING_INFO leakage).
    downstream_signals: dict[str, dict] = {}
    for upstream in ("ba", "techlead", "design"):
        up_role = next((r for r, k in _ROLE_TO_KEY.items() if k == upstream), upstream)
        total_asks = sum(
            count_clarifications_from_bus(bus, asker_role, up_role)
            for asker_role in _ROLE_TO_KEY if asker_role != up_role
        )
        downstream_signals[upstream] = {
            "clarif_count":           total_asks,
            "missing_info_downstream": missing_by_agent.get(upstream, 0),
        }
    adj.apply_downstream_signals(critic_reviews, downstream_signals)

    # 3) Cost penalty via detected scope.
    scope = detect_scope(task, project_context="")
    tokens_by_agent: dict[str, int] = {}
    for rec in tokens.records:
        tokens_by_agent[rec.agent] = tokens_by_agent.get(rec.agent, 0) + rec.total
    adj.apply_cost_penalty(critic_reviews, tokens_by_agent, scope)

    # 4) Short terminal summary of adjustments + full breakdown.
    if adj.adjustments:
        tprint(f"\n  {'═'*60}")
        tprint(f"  ⚖️  SCORE ADJUSTMENTS ({len(adj.adjustments)}) — real outcomes")
        tprint(f"  {'═'*60}")
        for a in adj.adjustments[:10]:
            tprint(f"  [{a['agent_key'].upper()}] {a['kind']}: {a['detail']}")
        tprint(f"  {'─'*60}")

    print_score_breakdown(
        critic_reviews=critic_reviews,
        adjustments=adj.adjustments,
        tprint=tprint,
    )

    # 5) Persist outcomes for learning.
    clarif_count_by_agent = {k: s["clarif_count"] for k, s in downstream_signals.items()}
    test_pass_rate = _patrol_test_pass_rate(patrol_result)
    budgets = load_cost_budgets(profile)
    expected = expected_budget_for_tasks(current_tasks or [], budgets)
    cost_ratio_by_agent: dict[str, float] = {}
    for role_name, toks in tokens_by_agent.items():
        key = _ROLE_TO_KEY.get(role_name, role_name.lower())
        exp = expected.get(key, 0) or 1
        cost_ratio_by_agent[key] = round(toks / exp, 3)

    logged = 0
    try:
        logged = log_session_outcomes(
            profile, session_id, critic_reviews,
            test_pass_rate=test_pass_rate,
            missing_info_by_agent=missing_by_agent,
            clarif_count_by_agent=clarif_count_by_agent,
            cost_ratio_by_agent=cost_ratio_by_agent,
        )
    except Exception as e:
        tprint(f"  ⚠️  outcome logging skipped: {type(e).__name__}: {e}")

    logged_skills = 0
    try:
        agent_skill_logs = {
            ak: list(getattr(ag, "_skill_usage_log", []))
            for ak, ag in (agents or {}).items()
        }
        logged_skills = log_session_skills(
            profile, session_id, agent_skill_logs, critic_reviews,
            test_pass_rate=test_pass_rate,
            missing_info_by_agent=missing_by_agent,
            clarif_count_by_agent=clarif_count_by_agent,
            cost_ratio_by_agent=cost_ratio_by_agent,
        )
    except Exception as e:
        tprint(f"  ⚠️  skill outcome logging skipped: {type(e).__name__}: {e}")

    return {
        "adjustments":   len(adj.adjustments),
        "logged":        logged,
        "logged_skills": logged_skills,
        "scope":         scope,
    }
