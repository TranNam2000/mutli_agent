"""
Thread-safe print — used everywhere orchestrator, session_manager, loggers,
and analyzer modules emit user-facing output.

Rule: NEVER call bare `print()` in production code. Always `from core.logging
import tprint`. This guarantees parallel Dev ∥ Test loops don't interleave
half-lines into each other.
"""
from __future__ import annotations

import sys
import threading

_print_lock = threading.Lock()


def tprint(*args, **kwargs) -> None:
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)
        # Flush so interactive output appears immediately even when stdout
        # is piped (CI, tee).
        sys.stdout.flush()
