"""
Cost-aware budgeting helpers.

Per-(agent, scope, complexity) expected-token budgets used by the rule evolver
to penalise agents who habitually blow past their budget. History is a simple
per-profile JSON file so we can trend over sessions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from core.io_utils import atomic_write_text
from core.paths import learning_dir


# Per-(agent, scope, complexity) token budgets — picked from empirical
# rule-of-thumb observations: BA summaries are small, Dev/Test outputs grow
# with complexity. Users can override via
# rules/<profile>/.learning/cost_budgets.json (flat keys "agent|scope|complexity").
COST_BUDGETS_DEFAULT: dict[tuple[str, str, str], int] = {
    ("ba",       "feature",  "S"):  1500,
    ("ba",       "feature",  "M"):  3000,
    ("ba",       "feature",  "L"):  6000,
    ("ba",       "feature",  "XL"): 12000,
    ("ba",       "bug_fix",  "S"):   800,
    ("ba",       "bug_fix",  "M"):  1500,
    ("ba",       "hotfix",   "S"):   600,
    ("ba",       "ui_tweak", "S"):   700,
    ("ba",       "refactor", "M"):  2500,
    ("design",   "feature",  "S"):  1500,
    ("design",   "feature",  "M"):  3000,
    ("design",   "feature",  "L"):  6000,
    ("design",   "feature",  "XL"): 10000,
    ("design",   "ui_tweak", "S"):   600,
    ("techlead", "feature",  "S"):  2000,
    ("techlead", "feature",  "M"):  4000,
    ("techlead", "feature",  "L"):  8000,
    ("techlead", "feature",  "XL"): 14000,
    ("techlead", "refactor", "M"):  3500,
    ("dev",      "feature",  "S"):  3000,
    ("dev",      "feature",  "M"):  8000,
    ("dev",      "feature",  "L"): 18000,
    ("dev",      "feature",  "XL"): 40000,
    ("dev",      "bug_fix",  "S"):  2000,
    ("dev",      "bug_fix",  "M"):  4500,
    ("dev",      "hotfix",   "S"):  1500,
    ("dev",      "ui_tweak", "S"):  1200,
    ("dev",      "refactor", "M"):  6000,
    ("test",     "feature",  "S"):  2000,
    ("test",     "feature",  "M"):  5000,
    ("test",     "feature",  "L"): 12000,
    ("test",     "feature",  "XL"): 25000,
    ("test",     "bug_fix",  "S"):  1200,
    ("test",     "hotfix",   "S"):   800,
}

COST_FALLBACK = 5000                  # used when key missing from table
COST_RATIO_OVER_BUDGET = 1.5          # current ratio ≥ this → "over"
COST_TREND_WINDOW = 5                 # look at last N sessions
COST_TREND_REQUIRED_OVER = 3          # need ≥ this many overs to emit suggestion


def _history_path(profile: str) -> Path:
    return learning_dir(profile) / "cost_history.json"


def _budgets_path(profile: str) -> Path:
    return learning_dir(profile) / "cost_budgets.json"


def load_cost_history(profile: str) -> dict:
    """Load per-agent cost ratio history. Returns {} if missing / unreadable."""
    path = _history_path(profile)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_cost_history(profile: str, history: dict) -> None:
    """Atomic write of the cost history JSON (crash-safe via tmp + rename)."""
    atomic_write_text(
        _history_path(profile),
        json.dumps(history, indent=2, ensure_ascii=False),
    )


def load_cost_budgets(profile: str) -> dict:
    """User override file (optional) merges over COST_BUDGETS_DEFAULT.

    User file format: flat dict with keys ``"agent|scope|complexity"``.
    """
    budgets: dict[tuple[str, str, str], int] = dict(COST_BUDGETS_DEFAULT)
    path = _budgets_path(profile)
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return budgets
        for k, v in user.items():
            parts = k.split("|")
            if len(parts) == 3:
                try:
                    budgets[(parts[0], parts[1], parts[2])] = int(v)
                except (TypeError, ValueError):
                    continue
    return budgets


def expected_budget_for_tasks(tasks: Iterable, budgets: dict) -> dict:
    """Sum expected tokens per agent for the given task batch.

    Tasks may be either dataclass-like (with ``get_metadata()``) or plain dicts.
    Missing metadata falls back to scope="feature", complexity="M".
    """
    totals: dict[str, int] = {}
    for t in tasks or []:
        try:
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
        except (AttributeError, KeyError, ValueError, TypeError):
            m = None
        scope      = (m.context.scope      if m else "feature")
        complexity = (m.context.complexity if m else "M")
        for agent_key in ("ba", "design", "techlead", "dev", "test"):
            exp = budgets.get((agent_key, scope, complexity), COST_FALLBACK)
            totals[agent_key] = totals.get(agent_key, 0) + exp
    return totals
