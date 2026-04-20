"""
Git integration for maintain mode.

Capabilities:
  - Create dedicated branch `multi-agent/<session_id>` before pipeline starts
  - Capture baseline diff-stat (what was uncommitted before we started)
  - After Dev: show what the pipeline changed (files + line count)
  - Optional auto-commit with a structured message per pipeline step
  - Safe rollback (git restore) if user aborts
"""
from __future__ import annotations
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitSnapshot:
    branch:          str
    head_sha:        str
    dirty_files:     list[str]   # uncommitted files at start
    created_branch:  str | None  # new branch we made (None if opted out)


@dataclass
class DiffReport:
    files_changed: int
    insertions:    int
    deletions:     int
    files:         list[str]
    raw_stat:      str


class GitHelper:
    def __init__(self, project_root: Path):
        self.root = Path(project_root)

    def _run(self, *args, check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=capture, text=True, timeout=30, check=check,
        )

    def is_repo(self) -> bool:
        try:
            r = self._run("rev-parse", "--is-inside-work-tree")
            return r.returncode == 0 and r.stdout.strip() == "true"
        except Exception:
            return False

    # ── Snapshot & branch ─────────────────────────────────────────────────────

    def snapshot(self, session_id: str, create_branch: bool = True) -> GitSnapshot:
        """Record starting state; optionally create a dedicated branch."""
        if not self.is_repo():
            return GitSnapshot(branch="(no-git)", head_sha="",
                               dirty_files=[], created_branch=None)

        branch = self._run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        head   = self._run("rev-parse", "HEAD").stdout.strip()
        dirty  = [l[3:] for l in self._run("status", "--porcelain").stdout.splitlines()
                  if l.strip()]

        new_branch = None
        if create_branch and not branch.startswith("multi-agent/"):
            new_branch = f"multi-agent/{session_id}"
            r = self._run("checkout", "-b", new_branch)
            if r.returncode != 0:
                # Branch exists? switch to it instead
                self._run("checkout", new_branch)

        return GitSnapshot(branch=branch, head_sha=head,
                           dirty_files=dirty, created_branch=new_branch)

    # ── Diff after pipeline edits ────────────────────────────────────────────

    def diff_since(self, snapshot: GitSnapshot) -> DiffReport:
        """Compute what changed since snapshot.head_sha (including unstaged)."""
        if not self.is_repo():
            return DiffReport(0, 0, 0, [], "(not a git repo)")

        # Numstat for staged + unstaged + untracked
        tracked = self._run("diff", "--numstat", snapshot.head_sha).stdout
        untracked_files = [l[3:] for l in self._run("status", "--porcelain").stdout.splitlines()
                           if l.startswith("??")]

        files: list[str] = []
        ins = dels = 0
        for line in tracked.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                a, d, path = parts
                try:
                    ins += int(a)
                    dels += int(d)
                except ValueError:
                    pass  # binary file
                files.append(path)

        for u in untracked_files:
            files.append(u + " (new)")

        # Also get a readable stat
        stat = self._run("diff", "--stat", snapshot.head_sha).stdout.strip()
        return DiffReport(
            files_changed=len(files),
            insertions=ins,
            deletions=dels,
            files=files[:40],
            raw_stat=stat[:3000],
        )

    # ── Commits ───────────────────────────────────────────────────────────────

    def stage_all(self) -> bool:
        if not self.is_repo():
            return False
        return self._run("add", "-A").returncode == 0

    def commit(self, message: str, allow_empty: bool = False) -> str | None:
        if not self.is_repo():
            return None
        cmd = ["commit", "-m", message]
        if allow_empty:
            cmd.append("--allow-empty")
        r = self._run(*cmd)
        if r.returncode == 0:
            return self._run("rev-parse", "HEAD").stdout.strip()
        return None

    def commit_step(self, step: str, session_id: str, description: str = "") -> str | None:
        """Structured commit per pipeline step."""
        self.stage_all()
        msg_lines = [f"[multi-agent/{step}] {description[:70]}" if description
                     else f"[multi-agent/{step}] auto-commit",
                     "",
                     f"Session: {session_id}",
                     f"Step: {step}"]
        return self.commit("\n".join(msg_lines))

    # ── Safety: rollback ──────────────────────────────────────────────────────

    def rollback_to(self, snapshot: GitSnapshot) -> bool:
        """Hard reset tracked files to snapshot, remove untracked ones."""
        if not self.is_repo():
            return False
        self._run("reset", "--hard", snapshot.head_sha)
        self._run("clean", "-fd")
        return True

    # ── Display ───────────────────────────────────────────────────────────────

    @staticmethod
    def format_diff(report: DiffReport) -> str:
        lines = [
            f"  📝 {report.files_changed} files changed  "
            f"+{report.insertions} / -{report.deletions}",
        ]
        for f in report.files[:10]:
            lines.append(f"     • {f}")
        if report.files_changed > 10:
            lines.append(f"     ... +{report.files_changed - 10} more")
        return "\n".join(lines)
