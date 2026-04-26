"""
SessionManager — owns session identity, output directory, and checkpoint I/O.

Extracted from `ProductDevelopmentOrchestrator` so session logic can be tested
and evolved independently of agent orchestration. The orchestrator keeps a
reference at `self.session_mgr` and delegates to it via a few thin methods
for backward compatibility (`_save`, `_checkpoint_path`, `_load_checkpoints`,
`_step_done`, `list_sessions`).

Checkpoint file format is unchanged — old sessions resume as before.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Iterable

from core.logging import tprint as _tprint   # canonical thread-safe print


# Steps whose output gets saved/resumed. Kept in sync with the orchestrator
# STEP_KEYS constant (list_sessions uses it to detect resumable sessions).
DEFAULT_STEP_KEYS: tuple[str, ...] = (
    "ba", "pm", "design", "techlead", "dev", "test_plan", "test",
)


class SessionManager:
    """
    Handles:
      - Session ID generation (timestamp + short random suffix — safe for concurrent runs)
      - Output directory resolution (in-project `.multi_agent/sessions/` when
        maintain mode, else `outputs/<project>/`)
      - Checkpoint save/load per step
      - Listing resumable sessions

    Does NOT handle:
      - Maintain-mode project detection (lives on orchestrator — uses agents)
      - Git state (orchestrator wires GitHelper)
      - Running the pipeline
    """

    def __init__(
        self,
        output_dir: str | Path = "outputs",
        *,
        project_name: str,
        resolved_output_dir: Path,
        resume_session: str | None = None,
        step_keys: Iterable[str] = DEFAULT_STEP_KEYS,
    ):
        self._output_dir_base = str(output_dir)
        self.project_name = project_name
        self.output_dir = Path(resolved_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.step_keys: tuple[str, ...] = tuple(step_keys)

        if resume_session:
            self.session_id = resume_session
        else:
            # Timestamp + short random suffix prevents collisions between
            # concurrent runs (same-second start).
            self.session_id = (
                datetime.now().strftime("%Y%m%d_%H%M%S")
                + "_" + secrets.token_hex(2)   # 4 hex chars = 65k values
            )

        self.results: dict[str, str] = {}

    # ── Checkpoint path resolution ────────────────────────────────────────────

    def checkpoint_path(self, key: str) -> Path:
        """Return the file path for a given step's checkpoint."""
        # New layout: inside .multi_agent/sessions/<id>/<prefix>_<step>.md
        if "sessions" in str(self.output_dir):
            from context import session_file
            return session_file(self.output_dir, self.session_id, key)
        # Legacy layout: outputs/<project>/<session>_<step>.md
        return self.output_dir / f"{self.session_id}_{key}.md"

    # ── Load / save ───────────────────────────────────────────────────────────

    def load_checkpoints(self) -> list[str]:
        """Populate self.results from any existing step files. Returns loaded keys."""
        loaded: list[str] = []
        for key in self.step_keys:
            path = self.checkpoint_path(key)
            if path.exists():
                content = path.read_text(encoding="utf-8")
                # Strip the header added by save()
                if "---" in content:
                    content = content.split("---", 1)[1].strip()
                self.results[key] = content
                loaded.append(key)
        if loaded:
            _tprint(f"  📂 Loaded checkpoints: {', '.join(loaded)}")
        return loaded

    def save(self, key: str, content: str) -> Path:
        """Write one step output to disk, with a short header. Returns the path."""
        path = self.checkpoint_path(key)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        wrapped = f"# {key.upper()} Output\n_Generated: {ts}_\n\n---\n\n{content}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wrapped, encoding="utf-8")
        _tprint(f"  💾 Saved → {path.name}")
        # Update in-memory cache so is_step_done() works without re-reading.
        self.results[key] = content
        return path

    def is_step_done(self, key: str) -> bool:
        return key in self.results

    # ── Resumable session discovery ───────────────────────────────────────────

    @classmethod
    def list_sessions(
        cls,
        output_dir: str = "outputs",
        project_name: str | None = None,
        step_keys: Iterable[str] = DEFAULT_STEP_KEYS,
    ) -> list[dict]:
        """Return resumable sessions under output_dir/[project_name]."""
        out = Path(output_dir)
        if not out.exists():
            return []

        keys = tuple(step_keys)

        # Determine which dirs to scan
        if project_name:
            scan_dirs = [out / project_name]
        else:
            scan_dirs = [d for d in out.iterdir() if d.is_dir()]
            if not scan_dirs:
                scan_dirs = [out]  # legacy flat layout fallback

        result: list[dict] = []
        for proj_dir in sorted(scan_dirs):
            sessions: dict[str, set] = {}

            # Legacy layout: outputs/<project>/<session_id>_<step>.md
            # New layout:    <project>/sessions/<session_id>/<NN_step>.md
            #                (or <project>/<session_id>/<NN_step>.md when the
            #                 project dir itself is under a "sessions" parent)
            # Both layouts are supported so resume works across user configs.
            for f in proj_dir.glob("*.md"):
                # Filename shape: <session_id>_<step>.md where step is the
                # LAST underscore-separated component (matches a known key).
                # session_id itself may contain underscores — e.g. the 3-part
                # form `YYYYMMDD_HHMMSS_<4hex>`.
                stem = f.stem
                matched_key = None
                for k in keys:
                    suffix = "_" + k
                    if stem.endswith(suffix):
                        matched_key = k
                        break
                if matched_key is None:
                    continue
                session_id = stem[: -(len(matched_key) + 1)]
                sessions.setdefault(session_id, set()).add(matched_key)

            # New layout scan: each direct child dir is a session folder
            # containing <prefix>_<step>.md files.
            for session_dir in proj_dir.iterdir() if proj_dir.exists() else []:
                if not session_dir.is_dir():
                    continue
                for f in session_dir.glob("*.md"):
                    # Name shape: <prefix>_<step>.md  (prefix is a 2-digit sort key)
                    stem = f.stem
                    matched_key = None
                    for k in keys:
                        # Match either "<prefix>_<key>" or bare "<key>"
                        if stem.endswith("_" + k) or stem == k:
                            matched_key = k
                            break
                    if matched_key:
                        sessions.setdefault(session_dir.name, set()).add(matched_key)

            for sid, done in sorted(sessions.items()):
                missing = [k for k in keys if k not in done]
                if missing:
                    prompt_text = ""
                    for candidate in proj_dir.glob(f"{sid}_prompt.txt"):
                        try:
                            prompt_text = candidate.read_text(encoding="utf-8").strip()
                        except (OSError, UnicodeDecodeError):
                            pass
                    result.append({
                        "session_id": sid,
                        "project":    proj_dir.name,
                        "completed":  [k for k in keys if k in done],
                        "missing":    missing,
                        "prompt":     prompt_text,
                    })
        return result
