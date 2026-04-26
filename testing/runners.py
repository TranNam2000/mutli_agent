"""
Test runners + code output savers — Patrol, Maestro, Flutter tests.

Extracted from orchestrator so the test integration stays isolated from
pipeline flow. Each function takes the orchestrator explicitly as `orch`.

Public API
----------
    save_implementation_files(orch, dev_output) -> None
    save_flutter_tests       (orch, review_output) -> None
    run_maestro_flows        (orch, flows) -> None
    run_patrol_tests         (orch, code, dev_agent, qa_agent) -> None
    resolve_project_dir      (orch) -> Path | None
"""
from __future__ import annotations

import re
from pathlib import Path

from core.logging import tprint


# Code-fence languages we recognise. Used by the compliance check below to
# detect when Dev still emits source blocks instead of editing files via
# Read/Edit/Write tools.
_RECOGNISED_FENCES = (
    "dart", "kotlin", "kt", "java", "swift", "ts", "tsx", "js", "jsx",
)


def save_implementation_files(orch, dev_output: str):
    """No-op + compliance check.

    With cwd=project_root wired into BaseAgent, Dev is expected to `Edit` /
    `Write` directly into the project. If the output still contains source
    code blocks, that's a prompt-compliance miss — warn loudly so the user
    can tighten the prompt, but do NOT write the blocks (that would create
    phantom duplicates next to whatever Dev already edited).
    """
    fences = "|".join(re.escape(f) for f in _RECOGNISED_FENCES)
    leaked = re.findall(rf"```({fences})\s*\n", dev_output, re.IGNORECASE)
    if leaked:
        tprint(
            f"\n  ⚠️  Dev emitted {len(leaked)} source code block(s) in its "
            f"reply ({', '.join(sorted(set(leaked)))}). Ignored — Dev should "
            f"use Edit/Write directly. Check the rule file if this keeps "
            f"happening."
        )


def save_flutter_tests(orch, review_output: str):
    """Extract Patrol Dart test + Maestro YAML flows from QA output, save, then auto-run."""
    orch._last_patrol_result = None
    orch._last_maestro_result = None

    # Patrol (Dart integration test)
    m_dart = re.search(r"```dart\s*(.*?)```", review_output, re.DOTALL)
    patrol_code = m_dart.group(1).strip() if m_dart else ""
    if patrol_code and len(patrol_code) > 50:
        dart_path = orch.output_dir / f"{orch.session_id}_patrol_test.dart"
        dart_path.write_text(patrol_code, encoding="utf-8")
        tprint(f"  🧪 Patrol test code → {dart_path.name}")

    # Maestro YAML flows — may have multiple blocks
    maestro_blocks: dict[str, str] = {}
    for i, m_yaml in enumerate(re.finditer(r"```ya?ml\s*(.*?)```", review_output, re.DOTALL), 1):
        content = m_yaml.group(1).strip()
        if "appId:" not in content:
            continue
        # Extract flow name from a header comment if present, else index
        name_m = re.search(r"#\s*maestro/([\w_/.\-]+\.yaml)", content)
        name = name_m.group(1).split("/")[-1] if name_m else f"flow_{i}.yaml"
        maestro_blocks[name] = content
    if maestro_blocks:
        yaml_dir = orch.output_dir / f"{orch.session_id}_maestro"
        yaml_dir.mkdir(parents=True, exist_ok=True)
        for name, content in maestro_blocks.items():
            (yaml_dir / name).write_text(content, encoding="utf-8")
        tprint(f"  🌊 Maestro flows ({len(maestro_blocks)}) → {yaml_dir.name}/")

    if not orch.maintain_mode:
        tprint(f"  ℹ️  không có project path — chạy thủ công: patrol test + maestro test")
        return

    # Auto-run on real project
    if patrol_code:
        run_patrol_tests(orch, 
            patrol_code,
            dev_agent=orch.agents.get("dev"),
            qa_agent=orch.agents.get("test"),
        )
    if maestro_blocks:
        run_maestro_flows(orch, maestro_blocks)


def run_maestro_flows(orch, flows: dict[str, str]):
    """Install and run Maestro YAML flows."""
    from testing.maestro_runner import MaestroRunner
    project_dir = resolve_project_dir(orch)
    if not project_dir:
        tprint(f"  ⚠️  No xác định is project dir — skip Maestro.")
        return
    runner = MaestroRunner(project_dir)
    if not runner.ensure_installed():
        return
    runner.install_flows(flows)
    result = runner.run_all()
    tprint(runner.format_suite_report(result))
    orch._last_maestro_result = result


def run_patrol_tests(orch, code: str, dev_agent=None, qa_agent=None):
    """
    Install Patrol test → run on Android + iOS → if fail: Dev fixes → re-run (max 2 rounds).
    """
    from testing.patrol_runner import PatrolRunner

    project_dir = resolve_project_dir(orch)
    if not project_dir:
        tprint(f"  ⚠️  Cannot determine project dir — skipping auto-run tests.")
        return

    tprint(f"\n  {'═'*60}")
    tprint(f"  🔨 PATROL AUTO-TEST — project: {project_dir}")
    tprint(f"  {'═'*60}")

    runner = PatrolRunner(project_dir)

    MAX_FIX_ROUNDS = 2
    for round_num in range(1, MAX_FIX_ROUNDS + 2):
        tprint(f"\n  ▶  Round {round_num} — chạy Patrol Android + iOS...")
        mr = runner.run_all_platforms(code)
        tprint(runner.format_multi_report(mr))
        orch._last_patrol_result = mr

        # Save combined report
        combined = "\n\n".join(
            f"=== {r.platform.upper()} ({r.device_name}) ===\n{r.raw_output}"
            for r in [mr.android, mr.ios] if r
        )
        report_path = orch.output_dir / f"{orch.session_id}_test_run_{round_num}.txt"
        report_path.write_text(combined, encoding="utf-8")

        if mr.all_passed:
            tprint(f"\n  ✅ Android + iOS đều PASSED — sẵn sàng bàn giao.")
            break

        if round_num > MAX_FIX_ROUNDS or not dev_agent or not qa_agent:
            tprint(f"\n  ❌ Tests still FAIL after {MAX_FIX_ROUNDS} time fix — need Dev xem thủ công.")
            tprint(f"     Report: {report_path.name}")
            break

        # Dev fixes based on actual device failures
        all_failures = mr.all_failures()
        tprint(f"\n  🔄 Dev fix dựa trên result thực tế (round {round_num}/{MAX_FIX_ROUNDS})...")
        failure_context = (
            "Flutter tests FAIL trên device thực tế:\n"
            + "\n".join(f"  {f}" for f in all_failures[:10])
            + f"\n\nRaw output:\n{combined[-2000:]}"
        )
        dev_agent._current_step = "dev_test_fix"
        if not orch._check_quota(f"Dev fix test failures round {round_num}"):
            break
        implementation = dev_agent.revise(
            orch.results.get("dev", ""),
            [failure_context],
            orch.results.get("ba", ""),
        )
        orch.results["dev"] = implementation
        orch._save("dev", implementation)

        # QA re-generates test code
        tprint(f"  🔄 QA update test code...")
        qa_agent._current_step = "test_review"
        test_plan_path = orch._checkpoint_path("test_plan")
        test_plan_doc  = (test_plan_path.read_text(encoding="utf-8")
                          if test_plan_path.exists() else "")
        new_review = qa_agent.review_implementation(test_plan_doc, implementation)
        new_m = re.search(r"```dart\s*(.*?)```", new_review, re.DOTALL)
        if new_m and len(new_m.group(1).strip()) > 50:
            code = new_m.group(1).strip()


def resolve_project_dir(orch) -> Path | None:
    """Find Flutter project directory from agents' project_context or cwd."""
    # Try agents that have project_context set
    for key in ["dev", "techlead", "ba"]:
        agent = orch.agents.get(key)
        ctx = getattr(agent, "project_context", "") or ""
        if not ctx:
            continue
        # Look for absolute path with pubspec.yaml
        for match in re.finditer(r"(/[/\w.\-]+)", ctx[:2000]):
            candidate = Path(match.group(1))
            if (candidate / "pubspec.yaml").exists():
                return candidate
    # Fallback: cwd if it has pubspec.yaml
    cwd = Path.cwd()
    if (cwd / "pubspec.yaml").exists():
        return cwd
    return None
