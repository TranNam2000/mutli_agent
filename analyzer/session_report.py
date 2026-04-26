"""
Session-level reporting — auto-feedback collection, HTML report writer.

Extracted from orchestrator so reporting changes don't require editing
pipeline flow code.

Public API
----------
    collect_auto_feedback (orch) -> None
    auto_run_feedback     (orch, feedback) -> None
    write_html_report     (orch) -> None
"""
from __future__ import annotations


from core.config import get_bool
from core.logging import tprint


def collect_auto_feedback(orch):
    """Build app + run Maestro + scrape logcat + diff screenshots → auto-trigger feedback mode."""
    orch._last_feedback_report = None
    project_dir = orch._resolve_project_dir()
    if not project_dir:
        tprint("  ℹ️  No project path — skipping auto-feedback.")
        return

    # Skip if user already set env var to opt out
    if get_bool("MULTI_AGENT_NO_AUTO_FEEDBACK"):
        tprint("  ℹ️  MULTI_AGENT_NO_AUTO_FEEDBACK=1 — skip auto-feedback.")
        return

    try:
        from testing.auto_feedback import AutoFeedback
    except ImportError:
        return

    tprint(f"\n  {'═'*60}")
    tprint(f"  💬 AUTO-FEEDBACK — build app + run E2E + scrape logs")
    tprint(f"  {'═'*60}")

    fb = AutoFeedback(project_dir)
    design_agent = orch.agents.get("design")
    design_specs = orch.results.get("design", "")

    def _vision_call(system, user, image_path):
        return design_agent._call_with_image(system, user, image_path)

    report = fb.collect(design_specs, _vision_call, platform="android")
    fb.print_report(report)
    orch._last_feedback_report = report

    # Auto-trigger feedback pipeline if blockers found
    if report.has_blockers and get_bool("MULTI_AGENT_AUTO_HEAL"):
        tprint("\n  🔁 BLOCKER detected — running feedback mode to auto self-heal...")
        feedback_payload = report.to_feedback_dict()
        if feedback_payload:
            try:
                # Use existing feedback pipeline but skip interactive prompts
                auto_run_feedback(orch, feedback_payload)
            except Exception as e:
                tprint(f"  ⚠️  Auto-heal failed: {e}")


def auto_run_feedback(orch, feedback: dict):
    """Non-interactive version of run_feedback — used by auto self-heal."""
    existing: dict[str, str] = {}
    for key in orch.STEP_KEYS:
        if key in orch.results:
            existing[key] = orch.results[key]

    if not existing:
        return

    ba = orch.agents["ba"]
    ba._current_step = "auto_feedback_assessment"
    assessment = ba.assess_feedback(
        feedback["description"], existing, feedback["type"]
    )
    tprint(f"  🎯 Auto-heal will re-run: {', '.join(assessment['affected'])}")

    # Load affected checkpoints marker — force re-run
    for key in assessment["affected"]:
        orch.results.pop(key, None)
        cp = orch._checkpoint_path(key)
        if cp.exists():
            cp.rename(cp.with_suffix(".md.before_autoheal"))

    # Re-run the pipeline with results already set for unchanged steps
    task = f"[AUTO-HEAL] {feedback['description'][:300]}"
    orch._run_task_based_pipeline(task)


def write_html_report(orch):
    """Generate HTML dashboard at outputs/<project>/<session>_REPORT.html"""
    try:
        from reporting.html_report import build_report
    except ImportError as e:
        tprint(f"  ⚠️  html_report unavailable: {e}")
        return

    skill_usage: list[dict] = []
    for agent in list(orch.agents.values()) + [orch.critic, orch.investigator]:
        skill_usage.extend(getattr(agent, "_skill_usage_log", []) or [])

    # Token summary
    by_agent: dict[str, int] = {}
    for rec in orch.tokens.records:
        by_agent[rec.agent] = by_agent.get(rec.agent, 0) + rec.total
    token_summary = {
        "budget":   orch.tokens.budget,
        "used":     orch.tokens.used,
        "pct":      orch.tokens.pct,
        "by_agent": by_agent,
    }

    out_path = build_report(
        session_id=orch.session_id,
        project_name=orch.project_name,
        profile=orch.profile,
        critic_reviews=orch.critic_reviews,
        skill_usage=skill_usage,
        token_summary=token_summary,
        patrol_result=getattr(orch, "_last_patrol_result", None),
        maestro_result=getattr(orch, "_last_maestro_result", None),
        feedback_report=getattr(orch, "_last_feedback_report", None),
        pipeline_steps=orch.STEP_KEYS,
        out_dir=orch.output_dir,
    )
    tprint(f"\n  📊 HTML Report → {out_path.name}")
    tprint(f"     Mở bằng: open {out_path}")
