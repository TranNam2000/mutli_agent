"""
Task-based pipeline runner — the main flow loop for building products from
a user idea.

Extracted from orchestrator._run_task_based_pipeline so the orchestrator can
stay a thin assembler. Each function takes the orchestrator explicitly as
`orch` so dependency + state are visible at the callsite.

Public API
----------
    run_task_based_pipeline(orch, product_idea, resources, allowed_steps, pm_route)
    qa_dev_loop            (orch, qa, dev, tl, ...)
    option_c_spec_postmortem(orch, tl, ba, dev, blockers, tasks, ...)
"""
from __future__ import annotations

import threading

from core.logging import tprint
from core.config import get_int


def run_task_based_pipeline(orch, product_idea: str,
                              resources: dict | None = None,
                              allowed_steps: list[str] | None = None,
                              pm_route: "RouteDecision | None" = None) -> dict:
    """
    New flow:
      1. BA.produce_tasks → classified task list (ui/logic/bug/hotfix/mixed)
      2. Split: UI tasks → Design.process_ui_tasks (find or create)
         → BA.consolidate_tasks (merge design refs)
      3. TechLead.prioritize_and_assign → SprintPlan with adjustments
      4. Parallel:
           Dev.implement per sprint in priority order
           Test.plan_from_sprint
      5. QA review → Dev fix loop per sprint
    """
    from pipeline.task_models import parse_tasks, split_by_type, TaskType

    ba   = orch.agents["ba"]
    des  = orch.agents["design"]
    tl   = orch.agents["techlead"]
    dev  = orch.agents["dev"]
    qa   = orch.agents["test"]

    def _allowed(step: str) -> bool:
        """True if step should run. None = legacy 'run everything' mode."""
        return allowed_steps is None or step in allowed_steps

    if allowed_steps is not None:
        tprint(f"\n  🧭 Sub-pipeline from PM: {' → '.join(allowed_steps)}")

    # ── STEP 1: BA builds classified task list ────────────────────────────
    if not _allowed("ba") and not orch._step_done("ba"):
        tprint("  ⏭️  BA skipped (not in PM dispatch plan)")
        tasks_md = ""
    elif orch._step_done("ba"):
        orch._skip("ba", "BA (task producer)")
        tasks_md = orch.results["ba"]
    else:
        if not orch._check_quota("BA task production"): return {}
        orch._header(1, 6, "BA (task producer)", "classify tasks...")
        ba._current_step = "ba"
        tasks_md = orch._run_with_review(
            "ba", ba,
            lambda: ba.produce_tasks(product_idea),
            original_prompt=product_idea,
        )
        orch._step_token_status("BA")

    tasks = parse_tasks(tasks_md) if tasks_md else []
    if not tasks:
        if allowed_steps is not None and "ba" not in allowed_steps:
            # BA was intentionally skipped — synthesize a placeholder task
            # so downstream Dev/Test still have something to chew on.
            from pipeline.task_models import Task, TaskType, Priority, Complexity, Risk, BusinessValue
            tasks = [Task(
                id="TASK-PM-001",
                title=(product_idea.strip().splitlines() or ["Request from PM"])[0][:80],
                description=product_idea,
                type=TaskType.LOGIC,
                priority=Priority.P2,
                complexity=Complexity.M,
                risk=Risk.MED,
                business_value=BusinessValue.NORMAL,
            )]
            tasks_md = tasks[0].to_markdown()
        else:
            tprint("\n  ❌ Could not parse any tasks from BA output — STOP.")
            tprint("     Check that BA output follows format `## TASK-XXX | type=... | priority=...` no.")
            return {}

    # Auto-split MIXED tasks into UI + Logic children
    from pipeline.task_models import expand_mixed_tasks
    mixed_count = sum(1 for t in tasks if t.type.value == "mixed")
    if mixed_count:
        tasks, _links = expand_mixed_tasks(tasks)
        tprint(f"\n  ✂️  Split {mixed_count} mixed tasks → {mixed_count*2} children "
               f"(UI blocking Logic)")

    # PM is authoritative for `scope` — override any BA-written value so
    # downstream gates / audit log / RuleOptimizer all agree with PM.
    pm_stamped = orch._apply_pm_metadata(pm_route, tasks)
    if pm_stamped:
        tprint(f"  🧭 PM stamped scope on {pm_stamped}/{len(tasks)} task(s) "
               f"(kind=`{pm_route.kind}`)")

    # Stash for the end-of-session cost-signal calculation.
    orch._current_tasks_for_cost = list(tasks)

    tprint(f"\n  📋 Parsed {len(tasks)} tasks from BA:")
    split = split_by_type(tasks)
    tprint(f"     UI: {len(split['ui'])}  Logic: {len(split['logic'])}  "
           f"Bug: {len(split['bug'])}  Hotfix: {len(split['hotfix'])}")

    # ── STEP 2: Design handles UI tasks (find or create) ─────────────────
    design_refs: dict[str, str] = {}
    ui_tasks = split["ui"]
    if not _allowed("design") and not orch._step_done("design"):
        tprint("  ⏭️  Design skipped (not in PM dispatch plan)")
    elif ui_tasks and not orch._step_done("design"):
        if not orch._check_quota("Design UI tasks"): return {}
        orch._header(2, 6, "Designer", f"processing {len(ui_tasks)} UI tasks (reuse or create)...")
        des._current_step = "design"
        des.detect_skill(product_idea,
                          task_metadata=orch._build_skill_metadata_summary(ui_tasks))
        existing_ds = orch.project_info and orch._find_existing_design_system() or ""
        design_refs = des.process_ui_tasks(ui_tasks, existing_ds)
        reused = sum(1 for r in design_refs.values() if r.startswith("[REUSE]"))
        created = len(design_refs) - reused
        tprint(f"  ✅ Design: {reused} reused, {created} newly created")

        # Save design output
        design_md = "# Design Refs per UI Task\n\n" + "\n\n".join(
            f"## {tid}\n{ref}" for tid, ref in design_refs.items()
        )
        orch._save("design", design_md)
        orch.results["design"] = design_md
        orch._step_token_status("Design")
    elif orch._step_done("design"):
        orch._skip("design", "Designer")

    # ── STEP 3: BA consolidates tasks with design refs ─────────────────────
    if design_refs:
        tprint(f"\n  🔄 BA consolidate {len(design_refs)} design refs into task list")
        tasks_md = ba.consolidate_tasks(tasks_md, design_refs)
        orch._save("ba", tasks_md)
        orch.results["ba"] = tasks_md
        # Re-parse to pick up design_ref fields
        tasks = parse_tasks(tasks_md)

    # ── STEP 4: TechLead prioritize + assign sprint ──────────────────────
    if not _allowed("techlead") and not orch._step_done("techlead"):
        tprint("  ⏭️  TechLead skipped (not in PM dispatch plan) — using tasks directly")
        sprint_md = tasks_md  # Dev will consume the raw task list instead.
        sprint_plan = None
    elif orch._step_done("techlead"):
        orch._skip("techlead", "Tech Lead (prioritizer)")
        sprint_md = orch.results["techlead"]
        sprint_plan = None
    else:
        if not orch._check_quota("TechLead prioritize"): return {}
        orch._header(3, 6, "Tech Lead (prioritizer)",
                     "evaluating resources + prioritize sprint...")
        tl._current_step = "techlead"
        tl.detect_skill(product_idea,
                         task_metadata=orch._build_skill_metadata_summary(tasks))
        # Role contribution: TL enriches metadata (impact_area + risk bump)
        # before its Critic gate is evaluated.
        if hasattr(tl, "enrich_metadata"):
            changed = tl.enrich_metadata(tasks)
            if changed:
                tprint(f"  🧠 TechLead enriched metadata on {changed} task(s)")

        # Option B — proactive spec review. Only fires when the regex
        # smell test flags at least one task, so spec-clean sessions
        # pay zero tokens. If BA answers, task ACs are patched in place
        # so downstream Dev/Test see the clarified spec.
        if hasattr(tl, "review_ba_spec_batch"):
            try:
                review = tl.review_ba_spec_batch(ba, tasks)
                flagged = review.get("flagged") or []
                if flagged:
                    tprint(f"  🗣  TL batch-reviewed BA spec: "
                           f"{len(flagged)} task(s) clarified via BA.")
                    # Update BA checkpoint so resume sees patched tasks.
                    orch.results["ba"] = "\n\n".join(t.to_markdown() for t in tasks)
                    orch._save("ba", orch.results["ba"])
            except Exception as e:
                tprint(f"  ⚠️  TL batch review failed: {e}")

        result = tl.prioritize_and_assign(tasks, resources)
        sprint_plan = result["sprint_plan"]
        sprint_md = result["summary_markdown"]
        orch._save("techlead", sprint_md)
        orch.results["techlead"] = sprint_md
        tprint(f"\n{sprint_plan.summary()}")
        if result["adjustments"]:
            tprint(f"\n  🔧 TechLead adjusted {len(result['adjustments'])} estimates")
        orch._step_token_status("TechLead")

        # Conditional Critic: gate by metadata (complexity, risk, impact).
        tl_ctx = {
            "tasks": tasks,
            # Legacy fields — kept for older callers & fallback logic.
            "tl_complexities": [t.complexity.value for t in tasks],
            "tl_types":        [t.type.value       for t in tasks],
        }
        sprint_md = orch._review_only(
            "techlead", tl, sprint_md,
            original_prompt=tasks_md, context=tl_ctx,
        )
        orch.results["techlead"] = sprint_md

    # ── STEP 5: parallel Test Plan + Dev ─────────────────────────────────
    if not _allowed("test_plan") and not orch._step_done("test_plan"):
        tprint("  ⏭️  Test plan skipped (not in PM dispatch plan)")
    elif not orch._step_done("test_plan"):
        if not orch._check_quota("Test plan from sprint"): return {}
        orch._header(4, 6, "QA (planner)", "writing test plan in sprint priority order...")
        qa._current_step = "test_plan"
        qa.detect_skill(product_idea,
                         task_metadata=orch._build_skill_metadata_summary(tasks))
        if sprint_plan is not None:
            test_plan = qa.plan_from_sprint(sprint_plan, tasks)
        else:
            # TechLead was skipped — plan from raw tasks.
            test_plan = qa.plan_from_sprint(None, tasks) if hasattr(qa, "plan_from_sprint") else ""
        orch._save("test_plan", test_plan)
        orch.results["test_plan"] = test_plan
        orch._step_token_status("TestPlan")

    if not _allowed("dev") and not orch._step_done("dev"):
        tprint("  ⏭️  Dev skipped (not in PM dispatch plan)")
    elif not orch._step_done("dev"):
        if not orch._check_quota("Dev implementation"): return {}
        orch._header(5, 6, "Developer", "implementing tasks in parallel...")
        dev._current_step = "dev"
        dev.detect_skill(product_idea,
                          task_metadata=orch._build_skill_metadata_summary(tasks))
        sorted_tasks = sorted(tasks, key=lambda x: -x.priority_score)
        design_snap = orch.results.get("design", "")
        test_plan_snap = orch.results.get("test_plan", "")
        impl_parts: list[tuple[int, str]] = []  # (idx, impl)
        qa_parts:   list[tuple[int, str]] = []  # (idx, review)
        lock = threading.Lock()
        _KIND_TO_STACK = {
            "flutter":  "Flutter/Dart",
            "node":     "Node.js/JavaScript",
            "python":   "Python",
            "react":    "React/JavaScript",
            "vue":      "Vue.js/JavaScript",
            "android":  "Android/Kotlin",
            "ios":      "iOS/Swift",
        }
        _dev_stack = _KIND_TO_STACK.get(
            (orch.project_info.kind if orch.project_info else ""), "Flutter/Dart"
        )

        def _impl_and_test(idx: int, task) -> None:
            task_md = task.to_markdown()
            tprint(f"    [{idx+1}/{len(sorted_tasks)}] dev → {task.id}: {(task.title or '')[:50]}")
            part = dev.implement_with_clarification(
                sprint_md, design_snap, task_md,
                tl_clarification="", tl_task_assignment=sprint_md,
                stack=_dev_stack,
            )
            review = qa.review_implementation(test_plan_snap, part, "")
            with lock:
                impl_parts.append((idx, part))
                qa_parts.append((idx, review))
            tprint(f"    [{idx+1}/{len(sorted_tasks)}] ✅ {task.id} done")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        max_workers = get_int("MULTI_AGENT_MAX_CONCURRENT", min_value=1, max_value=16)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_impl_and_test, i, t) for i, t in enumerate(sorted_tasks)]
            for f in as_completed(futures):
                f.result()  # re-raise any exception

        impl_parts.sort(key=lambda x: x[0])
        combined_impl = "\n\n".join(p for _, p in impl_parts)
        combined_review = "\n\n".join(r for _, r in sorted(qa_parts, key=lambda x: x[0]))

        orch._save("dev", combined_impl)
        orch.results["dev"] = combined_impl
        orch._save("test", combined_review)
        orch.results["test"] = combined_review
        orch._step_token_status("Dev")

        # ── STEP 6: QA→TechLead→Dev fix loop ─────────────────────────────
        if orch._check_quota("QA→TechLead→Dev fix loop"):
            orch._header(6, 6, "QA→TechLead→Dev", "fix loop for BLOCKERs...")
            combined_impl = qa_dev_loop(orch, 
                qa=qa, dev=dev, tl=tl,
                test_plan_doc=test_plan_snap,
                implementation=combined_impl,
                dev_clarification="",
                initial_review=combined_review,
                product_idea=product_idea,
                tasks=sorted_tasks,
            )

        impl = combined_impl
        orch._save_implementation_files(impl)

    # ── Wrap up flutter tests ─────────────────────────────────────────────
    orch._save_flutter_tests(orch.results.get("test", ""))

    # ── Show what the agents actually changed in the project ─────────────
    # When cwd=project_root is wired, agents may have used their native
    # Edit/Write tools — the only reliable signal of "what changed" is the
    # git diff since the pipeline started.
    if orch.git_helper and orch.git_snapshot and orch.git_helper.is_repo():
        try:
            diff = orch.git_helper.diff_since(orch.git_snapshot)
            if diff.files_changed:
                tprint(f"\n  📝 Agent changes vs baseline:")
                tprint(f"     • {diff.files_changed} file(s)  "
                       f"+{diff.insertions} / -{diff.deletions}")
                for f in diff.files[:15]:
                    tprint(f"       - {f}")
                if len(diff.files) > 15:
                    tprint(f"       … and {len(diff.files) - 15} more")
            else:
                tprint(f"\n  ℹ️  No git diff — agents didn't modify any tracked file.")
        except (OSError, AttributeError, ValueError) as e:
            tprint(f"  ⚠️  git diff skipped: {e}")

    # ── Wrap up ──────────────────────────────────────────────────────────
    orch._save_conversations()
    orch._save_summary()
    orch._collect_auto_feedback()
    orch._write_html_report()
    orch.bus.print_log()
    tprint(f"\n{'✅'*5}  TASK-BASED PIPELINE COMPLETE  {'✅'*5}")
    tprint(orch.tokens.full_report())
    return orch.results


def qa_dev_loop(orch,
    qa, dev, tl,
    test_plan_doc: str,
    implementation: str,
    dev_clarification: str,
    initial_review: str,
    product_idea: str,
    tasks: list | None = None,
) -> str:
    """
    QA finds BLOCKERs → reports to TechLead → TechLead triages → Dev fixes → QA re-verifies.
    Loops until:
      - No more BLOCKERs (success), OR
      - Same blockers appear 2 rounds in a row (no progress → escalate to user), OR
      - Token budget exhausted
    """
    review_output = initial_review
    prev_blocker_set: set[str] = set()
    round_num = 0
    audit_tasks = tasks or []

    while True:
        blockers = orch._extract_blockers(review_output)
        if not blockers:
            tprint(f"\n  ✅ QA: không có BLOCKER — implementation đạt request.")
            break

        # ── Emergency Audit Mode: only on the first iteration, and only
        # when some upstream Critic was actually skipped for these tasks.
        if round_num == 0 and orch._skipped_critic_by_task and not orch._emergency_audit:
            orch._trigger_emergency_audit(blockers, audit_tasks,
                                           agent_in_charge="Dev")

        round_num += 1
        tprint(f"\n  {'═'*60}")
        tprint(f"  🚨 QA→TechLead→Dev LOOP round {round_num} — {len(blockers)} BLOCKER(s):")
        for b in blockers[:4]:
            tprint(f"     • {b}")
        tprint(f"  {'═'*60}")

        # Detect no progress: same blockers as previous round
        current_set = set(b[:80] for b in blockers)
        spec_rescued = False
        if current_set == prev_blocker_set:
            # ── Option C — TL asks BA to reflect on spec before giving up.
            # Only fires once per session to avoid a BA ↔ TL chatter loop.
            if not getattr(orch, "_ba_postmortem_fired", False):
                spec_rescued = option_c_spec_postmortem(orch, 
                    tl, orch.agents["ba"], dev, blockers, audit_tasks,
                    implementation, product_idea,
                )
                orch._ba_postmortem_fired = True
            if not spec_rescued:
                tprint(f"\n  ⚠️  BLOCKERs unchanged after round {round_num} — "
                       f"manual intervention needed.")
                answer = input("  Keep trying to fix? [y/N] ").strip().lower()
                if answer != "y":
                    tprint("  ⏹  Stopping loop — BLOCKERs unresolved.")
                    break
        prev_blocker_set = current_set

        if not orch._check_quota(f"QA→TechLead→Dev fix round {round_num}"):
            break

        # QA báo TechLead → TechLead triage and giao fix task for Dev
        orch._dialogue_header(f"QA reports bugs to Tech Lead (round {round_num})")
        tl_fix_assignment = tl.triage_bugs(dev, blockers, implementation)

        # Auto-inject missing widget keys BEFORE generic revise
        missing_keys = orch._extract_missing_widget_keys(review_output)
        if missing_keys and hasattr(dev, "inject_widget_keys"):
            tprint(f"\n  🔑 Auto-inject {len(missing_keys)} missing widget keys:")
            for k in missing_keys[:5]:
                tprint(f"     • Key('{k['key']}') for {k['widget_type']} — {k['purpose'][:50]}")
            dev._current_step = "dev_inject_keys"
            implementation = dev.inject_widget_keys(implementation, missing_keys)
            orch._save("dev", implementation)
            orch.results["dev"] = implementation

        # Dev fix per task TechLead  triage
        fixes = orch._extract_fixes_required(review_output)
        fix_guide = fixes or blockers
        if tl_fix_assignment:
            fix_guide = [tl_fix_assignment] + fix_guide
        tprint(f"\n  🔄 Dev fixing per TechLead guidance...")
        dev._current_step = "dev_fix"
        implementation = dev.revise(implementation, fix_guide, product_idea)
        orch._save("dev", implementation)
        orch.results["dev"] = implementation
        orch._step_token_status("Dev fix")

        # QA re-verifies
        tprint(f"\n  🔄 QA re-verifying after Dev fix...")
        qa._current_step = "test_review"
        review_output = qa.review_implementation(test_plan_doc, implementation, dev_clarification)
        orch._save("test", review_output)
        orch.results["test"] = review_output
        orch._step_token_status("QA re-verify")

    return implementation


def option_c_spec_postmortem(orch, tl, ba, dev,
                                blockers: list[str], tasks: list,
                                implementation: str, product_idea: str) -> bool:
    """
    Option C — when Dev fix is stuck (same BLOCKER 2 rounds in a row), TL
    asks BA to reflect on whether the spec itself was incomplete. BA
    rewrites the affected tasks, Dev re-implements from the refined spec.

    Returns True if spec was actually refined and Dev re-ran; False if
    nothing useful came back and the caller should escalate.
    """
    orch._dialogue_header("TL → BA spec postmortem (stuck loop)")

    stuck_ids = [t.id for t in (tasks or [])][:5]
    question = (
        "Dev + QA đã stuck 2 vòng fix với các BLOCKER lặp lại:\n\n"
        + "\n".join(f"- {b[:120]}" for b in blockers[:4])
        + f"\n\nTask liên quan (suy luận): {', '.join(stuck_ids) or '(không rõ)'}"
        + "\n\nSpec gốc có thiếu AC, edge case, hoặc business rule nào không?"
        + " Nếu có, nêu cụ thể — TL sẽ nhờ bạn viết lại spec."
    )
    try:
        ba_reflection = tl.ask(ba, question)
    except Exception as e:
        tprint(f"  ⚠️  TL → BA postmortem failed: {e}")
        return False

    if not ba_reflection or len(ba_reflection) < 40:
        tprint("  ℹ️  BA reflection too short — nothing to act on.")
        return False

    # Ask BA to rewrite the full task list with the postmortem applied.
    current_ba_md = orch.results.get("ba", "")
    if not current_ba_md:
        return False
    if not hasattr(ba, "revise_specs"):
        return False
    try:
        new_ba_md = ba.revise_specs(current_ba_md, ba_reflection, stuck_ids)
    except Exception as e:
        tprint(f"  ⚠️  BA.revise_specs failed: {e}")
        return False
    if not new_ba_md or new_ba_md.strip() == current_ba_md.strip():
        tprint("  ℹ️  BA returned no meaningful changes — stopping postmortem.")
        return False

    # Persist the refined spec — resume/audit will see it.
    orch.results["ba"] = new_ba_md
    orch._save("ba", new_ba_md)
    tprint(f"  🔁 BA rewrote spec for stuck tasks — Dev re-implementing")

    # Dev re-implements using the refined spec as the fix guide.
    fix_guide = [
        "Spec was refined by BA after postmortem — re-implement the "
        "affected tasks per the NEW AC + edge cases below:",
        ba_reflection[:800],
    ]
    new_impl = dev.revise(implementation, fix_guide, product_idea)
    orch._save("dev", new_impl)
    orch.results["dev"] = new_impl
    return True
