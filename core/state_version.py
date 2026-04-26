"""
Schema-versioning for persistent learning state.

Rationale
---------
Files like ``.revise_history.json``, ``regression_model.json``,
``score_correlation.jsonl`` grow over months as the system learns. If we
ever change their shape (rename a key, split a list-of-tuples into
two lists, switch from per-agent counters to per-(agent, scope) counters,
…), an old file will silently misbehave — sometimes crashing, sometimes
applying the wrong rule, sometimes just regressing quietly.

This module centralises two things:

1. A **current schema version** per state file name, so we never write a
   file without stamping the version we wrote it with.
2. A **migration hook** (currently a no-op) — the place where, later,
   we'll implement "if you see version N, call ``_migrate_N_to_N1``".

Usage
-----
    from core.state_version import (
        CURRENT_REVISE_HISTORY_VERSION,
        stamp, detect_version, migrate_if_needed,
    )

    # On load
    raw = json.loads(path.read_text(encoding="utf-8"))
    version = detect_version(raw)
    raw = migrate_if_needed(raw, version, CURRENT_REVISE_HISTORY_VERSION,
                             schema="revise_history")

    # On save
    data = stamp(data, CURRENT_REVISE_HISTORY_VERSION)
    path.write_text(json.dumps(data, ...))

Important
---------
* ``stamp`` mutates **in place** and also returns the dict, so it works
  either way.
* ``detect_version`` returns ``0`` for "no version key found" — i.e.
  a file written before we introduced versioning. That's the signal that
  ``migrate_if_needed`` should treat as "starting point".
* No migrations exist yet. When the first one is needed, append a
  ``_migrate_revise_history_0_to_1`` (etc.) function and wire it up in
  the ``_MIGRATIONS`` map below.

Why a single module
-------------------
Keeping the versioning policy in one file means:
  (a) grep for ``CURRENT_`` shows you every versioned state at a glance;
  (b) future schema changes only need to touch one registry.
"""
from __future__ import annotations

from typing import Callable

# ── Current versions per state file (bump when schema changes) ──────────────
#
# Style: uppercase, CURRENT_<NAME>_VERSION. If you add a new state file,
# add its constant here AND register a migration slot in _MIGRATIONS below.

CURRENT_REVISE_HISTORY_VERSION   = 1
CURRENT_REGRESSION_MODEL_VERSION = 1
CURRENT_OUTCOME_LOG_VERSION      = 1       # for score_correlation.jsonl header
CURRENT_SKILL_OUTCOME_VERSION    = 1       # for skill_outcomes.jsonl header
CURRENT_COST_HISTORY_VERSION     = 1
CURRENT_SHADOW_VARIANTS_VERSION  = 1

SCHEMA_VERSION_KEY = "_schema_version"


# ── Public API ──────────────────────────────────────────────────────────────

def stamp(data: dict, version: int) -> dict:
    """Write the schema version into ``data`` in-place.

    Returns ``data`` for chaining convenience (``json.dumps(stamp(d, 2))``).
    """
    if not isinstance(data, dict):
        raise TypeError("stamp() only accepts dict payloads")
    data[SCHEMA_VERSION_KEY] = version
    return data


def detect_version(data: dict) -> int:
    """Return the ``_schema_version`` stored in ``data`` (0 if absent)."""
    if not isinstance(data, dict):
        return 0
    v = data.get(SCHEMA_VERSION_KEY, 0)
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def migrate_if_needed(data: dict, from_version: int, to_version: int,
                       schema: str) -> dict:
    """Apply any registered migrations for the given schema family.

    Currently every schema only has version 1 (i.e. no migration is
    needed). When a future change requires a migration, add a new
    ``_migrate_<schema>_<N>_to_<N+1>`` function and register it in
    :data:`_MIGRATIONS`.

    Behaviour:
      * If ``from_version == to_version``: return ``data`` unchanged.
      * If ``from_version == 0``: treat as "first time we're versioning" —
        stamp the current version, do not run migrations (the on-disk
        shape already matches v1 by definition).
      * Otherwise: walk the migration chain in order. Each step returns
        new data. If any step is missing, log once and return best-effort
        data (never raise — learning state missing a migration should
        NOT crash the pipeline).
    """
    if from_version == to_version:
        return data
    if from_version == 0:
        # Pre-versioning file; current on-disk shape is v1 by convention.
        return stamp(data, to_version)

    migrations = _MIGRATIONS.get(schema, {})
    current = from_version
    while current < to_version:
        step = migrations.get(current)
        if step is None:
            # No migration registered → give up gracefully.
            return stamp(data, current)
        try:
            data = step(data)
        except (KeyError, ValueError, TypeError, AttributeError):
            # A broken migration must not brick the learning loop.
            return stamp(data, current)
        current += 1
    return stamp(data, to_version)


# ── Migration registry (empty for now) ──────────────────────────────────────
#
# Shape: {"schema_name": {from_version: callable(data) -> data}}
# When you add a migration:
#   def _migrate_revise_history_1_to_2(data): ...
#   _MIGRATIONS["revise_history"] = {1: _migrate_revise_history_1_to_2}

_MIGRATIONS: dict[str, dict[int, Callable[[dict], dict]]] = {
    "revise_history":    {},
    "regression_model":  {},
    "outcome_log":       {},
    "skill_outcome":     {},
    "cost_history":      {},
    "shadow_variants":   {},
}
