"""
Session-level run variants — update existing session, replay feedback,
resume an interrupted run.

`run()` itself stays on the orchestrator (top-level public API), but these
three secondary entry points live here so the orchestrator's main class
body stays readable.

Public API
----------
    run_update   (orch, task, source_session) -> dict
    run_feedback (orch, source_session, feedback) -> dict
    run_resume   (orch) -> dict
"""
from __future__ import annotations

from core.logging import tprint


def run_update(orch, task: str, source_session: str) -> dict:
    """
    Update mode: read existing docs from source_session, assess impact,
    re-run only affected steps. Unchanged steps load from existing docs.
    """
    tprint(f"\n{'🔄'*5}  UPDATE MODE  {'🔄'*5}")
    tprint(f"  Task    : {task[:80]}")
    tprint(f"  Source  : {source_session}")

    # Load all existing docs from the source session
    existing: dict[str, str] = {}
    for key in orch.STEP_KEYS:
        path = orch.output_dir / f"{source_session}_{key}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            # Strip checkpoint header
            existing[key] = content.split("---", 1)[1].strip() if "---" in content else content
            tprint(f"  📄 Loaded: {key} ({len(existing[key])} chars)")
        else:
            tprint(f"  ⚠️  Missing: {key}")

    if not existing:
        tprint("  ❌ No docs found — running fresh pipeline instead.")
        return orch.run(task)

    # BA assesses impact
    tprint(f"\n  {'─'*60}")
    tprint(f"  🎯 BA evaluating impact...")
    ba = orch.agents["ba"]
    ba._current_step = "impact_assessment"
    assessment = ba.assess_impact(task, existing)
    ba.print_impact(assessment)

    # Confirm with user
    tprint(f"\n  Continue? [Enter] confirm / [E] edit affected steps list")
    choice = input("  > ").strip().upper()
    if choice == "E":
        raw = input(f"  Re-enter affected steps (e.g.: techlead,dev,test): ").strip()
        assessment["affected"]  = [s.strip() for s in raw.split(",") if s.strip()]
        assessment["unchanged"] = [s for s in orch.STEP_KEYS if s not in assessment["affected"]]

    # Pre-load unchanged steps from existing docs
    for key in assessment["unchanged"]:
        if key in existing:
            orch.results[key] = existing[key]
            tprint(f"  ✅ [{key:8}] Unchanged from session {source_session}")

    # Inject existing docs as context for affected agents
    existing_context = "\n\n".join(
        f"## Existing {k.upper()}\n{v[:1500]}"
        for k, v in existing.items()
        if k in assessment["unchanged"]
    )
    for key in assessment["affected"]:
        agent = orch.agents.get(key)
        if agent:
            agent.project_context = (
                f"## EXISTING DOCS (unchanged — use as context, do not duplicate)\n"
                f"{existing_context[:3000]}\n\n"
                + (agent.project_context or "")
            )

    # Clarification gate then run pipeline (skips pre-loaded steps)
    task = orch._clarification_gate(task)
    orch._run_task_based_pipeline(task)
    orch._run_rule_optimizer()
    return orch.results


def run_feedback(orch, source_session: str, feedback: dict) -> dict:
    """
    Feedback mode: collect issue from real product → re-run only affected steps.
    feedback: {type, description, screenshot_path?}
    """
    tprint(f"\n{'💬'*5}  FEEDBACK MODE  {'💬'*5}")
    tprint(f"  Type    : {feedback['type']}")
    tprint(f"  Session : {source_session}")
    tprint(f"  Issue   : {feedback['description'][:80]}")

    # Load existing docs
    existing: dict[str, str] = {}
    for key in orch.STEP_KEYS:
        path = orch.output_dir / f"{source_session}_{key}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            existing[key] = content.split("---", 1)[1].strip() if "---" in content else content
            tprint(f"  📄 Loaded: {key} ({len(existing[key])} chars)")

    if not existing:
        tprint("  ❌ No docs found — running fresh pipeline instead.")
        return orch.run(feedback["description"])

    # Screenshot analysis (UX issues / bugs with visual evidence)
    screenshot_analysis = ""
    screenshot_path = feedback.get("screenshot_path", "")
    if screenshot_path:
        tprint(f"\n  🖼️  Analyzing screenshot: {screenshot_path}")
        des = orch.agents["design"]
        design_doc = existing.get("design", "")
        try:
            screenshot_analysis = des._call_with_image(
                "Analyze screenshot from sản phẩm thực. So sánh with design specs and mô tả issue from user.",
                f"Design specs:\n{design_doc[:1500]}\n\nUser mô tả issue: {feedback['description']}",
                screenshot_path,
            )
            tprint(f"  ✅ Screenshot analyzed.")
            tprint(f"     {screenshot_analysis[:120]}...")
        except Exception as e:
            tprint(f"  ⚠️  Screenshot analysis failed: {e}")

    # Build feedback task description
    task_parts = [f"[{feedback['type']}] {feedback['description']}"]
    if screenshot_analysis:
        task_parts.append(f"\nUI Analysis from screenshot:\n{screenshot_analysis}")
    task = "\n".join(task_parts)

    # BA assesses which steps are affected
    tprint(f"\n  {'─'*60}")
    tprint(f"  🎯 BA evaluating feedback impact...")
    ba = orch.agents["ba"]
    ba._current_step = "feedback_assessment"
    assessment = ba.assess_feedback(task, existing, feedback["type"])
    ba.print_impact(assessment)

    # Confirm with user
    tprint(f"\n  Continue? [Enter] confirm / [E] edit affected steps list")
    choice = input("  > ").strip().upper()
    if choice == "E":
        raw = input(f"  Re-enter affected steps (e.g.: dev,test): ").strip()
        assessment["affected"]  = [s.strip() for s in raw.split(",") if s.strip()]
        assessment["unchanged"] = [s for s in orch.STEP_KEYS if s not in assessment["affected"]]

    # Pre-load unchanged steps
    for key in assessment["unchanged"]:
        if key in existing:
            orch.results[key] = existing[key]
            tprint(f"  ✅ [{key:12}] Unchanged")

    # Inject feedback context into affected agents
    feedback_context = (
        f"## 💬 PRODUCT FEEDBACK (from sản phẩm thực tế)\n"
        f"**Type:** {feedback['type']}\n"
        f"**Description:** {feedback['description']}\n"
        + (f"**Screenshot Analysis:** {screenshot_analysis}\n" if screenshot_analysis else "")
        + f"\n## Existing Docs (unchanged — use do context)\n"
        + "\n\n".join(
            f"### {k.upper()}\n{v[:800]}"
            for k, v in existing.items()
            if k in assessment["unchanged"]
        )
    )
    for key in assessment["affected"]:
        agent = orch.agents.get(key)
        if agent:
            agent.project_context = feedback_context[:3000] + "\n\n" + (agent.project_context or "")

    task = orch._clarification_gate(task)
    orch._run_task_based_pipeline(task)
    orch._run_rule_optimizer()
    return orch.results


def run_resume(orch) -> dict:
    completed = [k for k in orch.STEP_KEYS if orch._step_done(k)]
    missing   = [k for k in orch.STEP_KEYS if not orch._step_done(k)]
    tprint(f"\n{'🔄'*5}  RESUME PIPELINE  {'🔄'*5}")
    tprint(f"  Session  : {orch.session_id}")
    tprint(f"  ✅ Done  : {', '.join(completed) or 'none'}")
    tprint(f"  ⏳ Resume: {', '.join(missing) or 'none'}\n")
    # Reload the BA output so we can reconstruct task list and continue
    ba_md = orch.results.get("ba", "")
    orch._run_task_based_pipeline(product_idea=ba_md[:400] or "resume")
    orch._run_rule_optimizer()
    return orch.results
