"""
Shared pytest fixtures.

Most tests need either an isolated temp profile (so we don't pollute
rules/default/.learning/) or an LLM-free BaseAgent stub. Both live here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path when tests run from tests/ directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def tmp_profile(tmp_path, monkeypatch):
    """Redirect analyzer/* helpers to a throwaway profile inside tmp_path.

    Returns the profile name (always "testprof"). All .learning/*.jsonl writes
    go under tmp_path/rules/testprof/.learning/.
    """
    profile = "testprof"

    # Make core.paths point to tmp_path instead of the real repo.
    import core.paths as paths
    monkeypatch.setattr(paths, "RULES_DIR",   tmp_path / "rules")
    monkeypatch.setattr(paths, "SKILLS_DIR",  tmp_path / "skills")
    monkeypatch.setattr(paths, "OUTPUTS_DIR", tmp_path / "outputs")

    # Also patch functions that captured RULES_DIR at import time in other
    # modules. Each helper re-derives from `learning_dir(profile)` so we
    # need to re-bind through the module's own lookup.
    for mod_name in ("analyzer.outcome_logger",
                     "analyzer.skill_outcome_logger",
                     "analyzer.cost_history",
                     "analyzer.regression_classifier"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "learning_dir"):
            monkeypatch.setattr(mod, "learning_dir",
                                lambda p, tmp=tmp_path: tmp / "rules" / p / ".learning")

    (tmp_path / "rules" / profile / ".learning").mkdir(parents=True, exist_ok=True)
    return profile


@pytest.fixture
def fake_review():
    """Single critic review dict matching the orchestrator's shape."""
    return {
        "agent_key":  "dev",
        "agent_role": "Developer",
        "score":      8,
        "round":      1,
        "verdict":    "PASS",
        "output":     "",
    }


@pytest.fixture
def fake_reviews():
    """Multi-agent review set."""
    return [
        {"agent_key": "ba",       "agent_role": "BA",        "score": 7, "round": 1},
        {"agent_key": "design",   "agent_role": "Designer",  "score": 8, "round": 1},
        {"agent_key": "techlead", "agent_role": "Tech Lead", "score": 7, "round": 1},
        {"agent_key": "dev",      "agent_role": "Developer", "score": 8, "round": 1, "output": ""},
        {"agent_key": "test",     "agent_role": "QA",        "score": 7, "round": 1},
    ]
