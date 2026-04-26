"""
Atomic file I/O helpers.

Why
---
Every state file we persist (`.revise_history.json`, `regression_model.json`,
cost history, …) must survive crashes mid-write. Partial writes are
particularly bad for learning state, because corrupt JSON silently
resets all accumulated signal to zero.

Standard pattern: write to a sibling ``*.tmp`` file, then ``os.replace``
onto the target. On POSIX filesystems the rename is atomic — readers
either see the old file or the new file, never a half-written one.

Before this module, the pattern was duplicated across 4 sites. Keeping
it in one place means:
  * one audit point for subtle bugs (mkdir parent, encoding, suffix);
  * trivially extendable when we need e.g. fsync-before-rename for
    extra durability on flaky filesystems.

Usage
-----
    from core.io_utils import atomic_write_text

    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
"""
from __future__ import annotations

from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically.

    Steps:
      1. Ensure the parent directory exists.
      2. Write to ``<path>.tmp`` first (sibling, same filesystem so the
         rename is atomic).
      3. ``Path.replace`` — atomic rename on POSIX; on Windows it maps
         to ``os.replace`` which is also atomic.

    Any pre-existing file at ``path`` is clobbered — this is intentional
    behaviour for state files where the new value must fully supersede
    the old one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)
