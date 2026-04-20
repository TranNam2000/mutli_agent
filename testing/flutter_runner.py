"""Deprecated — use testing.patrol_runner instead.

Kept as a shim so old imports don't break. Re-exports symbols from patrol_runner.
"""
from warnings import warn
warn("flutter_runner is deprecated — use testing.patrol_runner", DeprecationWarning)

from testing.patrol_runner import (  # noqa: F401
    PatrolRunner as FlutterRunner,
    PatrolResult as FlutterTestResult,
    PatrolMultiResult as MultiPlatformResult,
)
