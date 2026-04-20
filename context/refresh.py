"""
Context refresh manager — detect stale context during long-running sessions.

When pipeline runs > REFRESH_THRESHOLD_S, re-scan source files for mtime
changes. If any tracked file was modified externally, rebuild the scoped
context before the next step so agents see fresh code.

Typical triggers:
  - User edited file manually during pipeline
  - Git pull mid-session (another dev pushed)
  - Dev step itself generated new files (should show up in later steps)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path


REFRESH_THRESHOLD_S = 1800  # 30 minutes
MTIME_CHECK_WINDOW = 60     # scan files changed in last 60s


@dataclass
class ContextWatermark:
    started_at:    float                       # monotonic time pipeline started
    file_mtimes:   dict[str, float] = field(default_factory=dict)  # path → last-known mtime
    last_refresh:  float = 0.0                 # monotonic time of last refresh

    def should_check(self) -> bool:
        """True if enough time elapsed to warrant mtime scan."""
        now = time.monotonic()
        if now - self.started_at < REFRESH_THRESHOLD_S:
            return False
        # Throttle: max 1 check per 5 minutes
        return (now - self.last_refresh) >= 300

    def mark_checked(self):
        self.last_refresh = time.monotonic()


class ContextRefresher:
    def __init__(self, project_root: Path, watched_extensions: set[str] | None = None):
        self.project_root = Path(project_root)
        self.exts = watched_extensions or {".dart", ".ts", ".tsx", ".js", ".jsx", ".py", ".go"}
        self.watermark = ContextWatermark(started_at=time.monotonic())

    def snapshot(self, paths: list[Path]):
        """Record current mtimes for a set of files."""
        for p in paths:
            try:
                self.watermark.file_mtimes[str(p)] = p.stat().st_mtime
            except (OSError, ValueError):
                continue

    def detect_changes(self) -> list[Path]:
        """Return paths whose mtime moved since snapshot."""
        changed: list[Path] = []
        for path_str, old_mtime in list(self.watermark.file_mtimes.items()):
            p = Path(path_str)
            try:
                new_mtime = p.stat().st_mtime
            except OSError:
                # File deleted
                changed.append(p)
                continue
            if new_mtime > old_mtime + 1:  # 1s tolerance
                changed.append(p)
                self.watermark.file_mtimes[path_str] = new_mtime
        self.watermark.mark_checked()
        return changed

    def scan_recent_changes(self) -> list[Path]:
        """Find files modified in last MTIME_CHECK_WINDOW seconds (broader scan)."""
        now = time.time()
        recent: list[Path] = []
        for f in self.project_root.rglob("*"):
            if not f.is_file() or f.suffix not in self.exts:
                continue
            if any(part.startswith(".") for part in f.relative_to(self.project_root).parts):
                continue  # skip .git, .multi_agent etc.
            try:
                if now - f.stat().st_mtime < MTIME_CHECK_WINDOW:
                    recent.append(f)
            except OSError:
                continue
        return recent

    def need_refresh(self) -> tuple[bool, list[Path]]:
        """Returns (should_refresh, changed_files)."""
        if not self.watermark.should_check():
            return False, []
        changed = self.detect_changes()
        return bool(changed), changed
