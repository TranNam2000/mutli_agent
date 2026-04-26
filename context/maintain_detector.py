"""
Maintain-mode detection + project context loader.

Extracted from orchestrator so these file-system-heavy helpers can be
tested with tmp_path fixtures without spinning up the full pipeline.

Public API
----------
    auto_detect_maintain         (orch) -> None
    detect_maintain_from_task    (orch, task) -> None
    load_project_context         (orch, maintain_dir, task_hint) -> None
    find_existing_design_system  (orch) -> str
"""
from __future__ import annotations

import json
from pathlib import Path

from core.logging import tprint


def auto_detect_maintain(orch):
    """Auto-detect maintain mode when working directory has recognizable project files."""
    PIPELINE_DIR = Path(__file__).resolve().parent
    # Mobile/web project signals — intentionally excludes requirements.txt / pyproject.toml
    # so running from inside the pipeline dir itself never triggers maintain mode.
    signals   = ["pubspec.yaml", "package.json", "build.gradle", "pom.xml",
                 "Cargo.toml", "go.mod"]
    code_dirs = ["lib", "src", "app", "packages"]

    # Search cwd first, then parent dirs (up to 3 levels) — skipping the pipeline dir itself
    search_paths = []
    candidate = Path.cwd().resolve()
    for _ in range(4):
        if candidate != PIPELINE_DIR:
            search_paths.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    for path in search_paths:
        has_signal = any((path / s).exists() for s in signals)
        has_code   = any((path / d).is_dir() for d in code_dirs)
        if has_signal or has_code:
            tprint(f"\n  🔍 Auto-detected project in {path} — activating maintain mode")
            orch._maintain_dir = str(path)
            # Update project name & output dir to reflect the real project
            from context.project_context_reader import detect_project_name
            orch.project_name = detect_project_name(path)
            orch.output_dir = Path(orch._output_dir_base) / orch.project_name
            orch.output_dir.mkdir(parents=True, exist_ok=True)
            load_project_context(orch, str(path))
            return


def detect_maintain_from_task(orch, task: str):
    """Detect maintain mode from task description keywords (fix, bug, investigate, etc.)."""
    maintain_keywords = [
        "fix", "bug", "bug", "fix", "investigate", "investigate", "debug",
        "broken", "not working", "doesn't work", "not working",
        "update existing", "update", "refactor", "regression",
        "crash", "error in", "issue with", "problem with",
    ]
    task_lower = task.lower()
    matched = [kw for kw in maintain_keywords if kw in task_lower]
    if matched:
        tprint(f"\n  🔍 Task keywords {matched[:2]} suggest maintain mode — checking cwd...")
        auto_detect_maintain(orch)


def load_project_context(orch, maintain_dir: str, task_hint: str = ""):
    """
    Maintain-mode setup:
      1. Detect project (handles monorepos, picks right subproject)
      2. Build SCOPED context (keyword-driven, not blindly reading 60 files)
      3. Initialize Git helper + branch
      4. Run health check to capture baseline
      5. Save context inside .multi_agent/sessions/<id>/
    """
    from context import (
        detect_project, build_scoped_context, GitHelper, HealthChecker,
        session_file, save_context,
    )

    tprint(f"\n  🔍 Reading project context from: {maintain_dir}")
    if task_hint:
        tprint(f"  🎯 Task hint: \"{task_hint[:60]}\" — scoped reading")

    # 1. Detect project
    orch.project_info = detect_project(maintain_dir, task_hint=task_hint)
    if orch.project_info:
        if orch.project_info.is_monorepo:
            tprint(f"  🌳 Monorepo detected: {len(orch.project_info.subprojects)} subprojects")
        tprint(f"  📦 Project: {orch.project_info}")

    # 2. Scoped context
    project = orch.project_info
    if project and task_hint:
        context = build_scoped_context(project, task_hint)
        strategy = "scoped (keyword-driven)"
    else:
        # Fallback to full scan when no task hint yet
        context_path = session_file(orch.output_dir, orch.session_id, "inputs",
                                     extension="tmp")
        context = save_context(
            project.root if project else maintain_dir,
            context_path, task_hint=task_hint,
        )
        context_path.unlink(missing_ok=True)
        strategy = "full scan"

    # Save context file inside session folder
    ctx_path = session_file(orch.output_dir, orch.session_id,
                             "inputs", extension="md")
    ctx_path.write_text(
        f"# Project Context (strategy: {strategy})\n\n{context}",
        encoding="utf-8",
    )
    tprint(f"  📄 Context [{strategy}] → {ctx_path.name} ({len(context):,} chars)")

    # 3. Git integration
    if project:
        orch.git_helper = GitHelper(project.root)
        if orch.git_helper.is_repo():
            orch.git_snapshot = orch.git_helper.snapshot(
                orch.session_id, create_branch=True,
            )
            tprint(f"  🌿 Git branch: {orch.git_snapshot.created_branch or orch.git_snapshot.branch}")
            if orch.git_snapshot.dirty_files:
                tprint(f"  ⚠️  {len(orch.git_snapshot.dirty_files)} files uncommitted "
                       f"before pipeline start — will not commit")

    # 4. Health check baseline (skip on resume — already done last time)
    if project and not orch.results:
        try:
            checker = HealthChecker(project, timeout_s=60)
            orch.health_report = checker.run(skip_tests=False)
            HealthChecker.print_report(orch.health_report)
            # Cache baseline so Dev can diff later
            from context import resolve_cache_dir
            cache = resolve_cache_dir(project.root)
            (cache / "health_baseline.json").write_text(
                json.dumps({
                    "session":          orch.session_id,
                    "errors":           orch.health_report.analyze_errors,
                    "warnings":         orch.health_report.analyze_warnings,
                    "test_failed":      orch.health_report.test_failed,
                    "test_passed":      orch.health_report.test_passed,
                    "baseline_issues":  list(orch.health_report.baseline_issues),
                }, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            tprint(f"  ⚠️  Health check skipped: {e}")

    # 5. Inject context + cwd into agents. cwd makes the claude CLI run
    #    *inside* the project dir so its native Read/Edit/Write/Bash tools
    #    operate on the user's project without us parsing code blocks.
    project_root = str(project.root) if project else None
    for key in ["ba", "techlead", "dev", "test"]:
        orch.agents[key].project_context = context
        if project_root:
            orch.agents[key].cwd = project_root
    orch.investigator.project_context = context
    if project_root:
        orch.investigator.cwd = project_root
        # Propagate to every other agent registered on orch (critic / rule /
        # skill designer) so their LLM calls also anchor to the project.
        for extra in (getattr(orch, "critic", None),
                      getattr(orch, "rule_optimizer", None),
                      getattr(orch, "skill_designer", None),
                      orch.agents.get("pm"), orch.agents.get("design")):
            if extra is not None:
                extra.cwd = project_root
    orch.maintain_mode = True
    if project:
        orch._maintain_dir = str(project.root)

    # 6. Start context refresher for long-running sessions
    if project:
        from context import ContextRefresher
        orch._context_refresher = ContextRefresher(project.root)
        # Snapshot all source files currently relevant
        try:
            watched = list(project.root.rglob("*"))
            relevant = [p for p in watched
                        if p.is_file() and p.suffix in orch._context_refresher.exts
                        and not project.should_skip(p)][:200]
            orch._context_refresher.snapshot(relevant)
        except (OSError, UnicodeDecodeError):
            pass


def find_existing_design_system(orch) -> str:
    """Scan project context for design system / theme / token files."""
    from context import build_scoped_context
    if not orch.project_info:
        return ""
    try:
        return build_scoped_context(
            orch.project_info,
            task="design system tokens colors typography theme",
            max_total_chars=8000,
        )
    except (ValueError, KeyError, AttributeError, OSError):
        return ""
