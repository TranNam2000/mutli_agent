"""
Output path manager — keep all pipeline artifacts inside the user's project
under `.multi_agent/` so the user never has to go into multi_agent/outputs/.

Layout inside each project:
  <project>/
    .multi_agent/
      .gitignore                           # auto-generated to ignore itself
      sessions/<session_id>/
        00_inputs.md
        10_ba.md
        20_design.md
        30_techlead.md
        40_test_plan.md
        50_dev.md
        60_test.md
        99_SUMMARY.md
        REPORT.html
        project_context.md
        conversations.md
        maestro/*.yaml
        patrol_test.dart
        feedback/*.png
      cache/
        health_baseline.json
        skill_history.json  (profile-local)
"""
from __future__ import annotations
from pathlib import Path


MULTI_AGENT_DIRNAME = ".multi_agent"


def resolve_output_dir(project_root: Path | str | None, fallback: Path) -> Path:
    """
    Return the session output directory:
      - If project_root is given → <project_root>/.multi_agent/sessions/
      - Else → fallback (usually multi_agent/outputs/<project_name>/)
    """
    if project_root:
        base = Path(project_root) / MULTI_AGENT_DIRNAME / "sessions"
        base.mkdir(parents=True, exist_ok=True)
        _ensure_gitignore(Path(project_root) / MULTI_AGENT_DIRNAME)
        return base
    return Path(fallback)


def resolve_cache_dir(project_root: Path | str) -> Path:
    cache = Path(project_root) / MULTI_AGENT_DIRNAME / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _ensure_gitignore(multi_agent_dir: Path):
    """Auto-create .gitignore inside .multi_agent/ and append root .gitignore."""
    gi = multi_agent_dir / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "# multi-agent pipeline artifacts — do not commit\n"
            "*\n!.gitignore\n",
            encoding="utf-8",
        )

    # Also add entry to project root's .gitignore if missing
    root_gi = multi_agent_dir.parent / ".gitignore"
    entry = ".multi_agent/"
    if root_gi.exists():
        content = root_gi.read_text(encoding="utf-8", errors="ignore")
        if entry not in content and ".multi_agent" not in content:
            with root_gi.open("a", encoding="utf-8") as f:
                f.write(f"\n# Multi-agent pipeline outputs\n{entry}\n")


# ── Session-specific paths ────────────────────────────────────────────────────

STEP_PREFIX = {
    "inputs":     "00",
    "ba":         "10",
    "design":     "20",
    "techlead":   "30",
    "test_plan":  "40",
    "dev":        "50",
    "test":       "60",
    "investigation": "05",
    "SUMMARY":    "99",
}


def session_file(session_dir: Path, session_id: str, step: str,
                 extension: str = "md") -> Path:
    """Return the canonical path for a pipeline step output.

    Uses `<prefix>_<step>.md` naming (e.g. `10_ba.md`) so listing the dir
    shows artifacts in pipeline order.
    """
    session_folder = session_dir / session_id
    session_folder.mkdir(parents=True, exist_ok=True)
    prefix = STEP_PREFIX.get(step, "")
    name = f"{prefix}_{step}.{extension}" if prefix else f"{step}.{extension}"
    return session_folder / name
