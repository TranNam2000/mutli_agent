"""
Flow tests for learning.rule_runner / rule_lifecycle.

These exercise the dispatch lanes of `run_rule_optimizer`:
  1. empty signals                     → short-circuit skip
  2. LLM + integrity suggestions       → routed to RuleEvolver
  3. regressed rule                    → rollback_regressed_rules calls rollback
  4. classifier gate (legacy path)     → `apply`/`shadow`/`skip` decisions

Everything that would hit the LLM or disk is stubbed. The history file and
RULES_DIR both live inside `tmp_profile`, so no real repo state is touched.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from learning import rule_runner
from learning import rule_lifecycle
from learning.revise_history import ReviseHistory


# ── Stubs ────────────────────────────────────────────────────────────────────

class _StubRuleOptimizer:
    """Pretend RuleOptimizerAgent — returns canned suggestion shape."""
    def __init__(self, llm_suggestions=None, integrity_suggestions=None):
        self._llm = llm_suggestions or []
        self._integ = integrity_suggestions or []
        self.apply_calls = 0
        self.rollback_calls = 0
        self._last_backup = "/tmp/fake-backup.md"
    def analyze_and_suggest(self, revise_reviews, chronic_patterns,
                             history=None, easy_items=None):
        return list(self._llm)
    def suggest_from_integrity(self, integrity):
        return list(self._integ)
    def apply(self, suggestion):
        self.apply_calls += 1
        return self._last_backup
    def rollback(self, backup_path, rule_path):
        self.rollback_calls += 1
        return True


class _StubTokens:
    """Minimum TokenTracker surface used by build_cost_suggestions."""
    def __init__(self):
        self.records = []  # empty → cost suggestions shortcircuit


def _make_orch(tmp_profile, critic_reviews=None, llm_sugs=None, integ_sugs=None):
    """Assemble a minimum orchestrator surface that run_rule_optimizer uses."""
    return SimpleNamespace(
        profile=tmp_profile,
        session_id="20260101_120000_abcd",
        critic_reviews=list(critic_reviews or []),
        rule_optimizer=_StubRuleOptimizer(llm_sugs, integ_sugs),
        _integrity=None,
        tokens=_StubTokens(),
        _current_tasks_for_cost=[],
    )


# ── Happy-path: no signals, no work ──────────────────────────────────────────

def test_run_rule_optimizer_skips_when_no_signals(tmp_profile, monkeypatch):
    """No REVISE reviews + no chronic patterns + no easy items → skip early
    without calling LLM."""
    orch = _make_orch(tmp_profile)

    # Sentinel: if analyze_and_suggest were called, test would still pass
    # (empty suggestions) but we want to assert it never fired.
    called = {"n": 0}
    orch.rule_optimizer.analyze_and_suggest = lambda *a, **kw: (  # type: ignore[assignment]
        called.__setitem__("n", called["n"] + 1) or [])

    rule_runner.run_rule_optimizer(orch)

    assert called["n"] == 0
    assert orch.rule_optimizer.apply_calls == 0


# ── Route to RuleEvolver (default) ───────────────────────────────────────────

def test_run_rule_optimizer_routes_through_rule_evolver(tmp_profile, monkeypatch):
    """With a REVISE review present, the default branch must route through
    run_rule_evolver (not run_legacy_rule_optimizer)."""
    orch = _make_orch(
        tmp_profile,
        critic_reviews=[{
            "agent_key": "dev", "verdict": "REVISE", "score": 5,
            "strengths": [], "weaknesses": ["missing error handling"],
            "checklist_flat": [], "checklist_answers": {},
        }],
        llm_sugs=[{
            "agent_key": "dev", "reason": "missing error handling",
            "addition": "Always wrap async calls in try/except.",
            "target_type": "rule", "rule_path": Path("/nowhere/dev.md"),
            "profile": tmp_profile, "source": "llm",
        }],
    )

    routed = {"evolver": 0, "legacy": 0}
    monkeypatch.setattr(rule_runner, "run_rule_evolver",
                         lambda o, s, h: routed.__setitem__("evolver", routed["evolver"] + 1))
    monkeypatch.setattr(rule_runner, "rollback_regressed_rules",
                         lambda o, h: None)
    monkeypatch.setattr(rule_runner, "maybe_upgrade_criteria",
                         lambda o, h: [])
    # Legacy path should NOT fire by default.
    monkeypatch.setattr(rule_runner, "run_legacy_rule_optimizer",
                         lambda o, s, h: routed.__setitem__("legacy", routed["legacy"] + 1))

    rule_runner.run_rule_optimizer(orch)

    assert routed["evolver"] == 1
    assert routed["legacy"] == 0


def test_legacy_path_when_env_var_set(tmp_profile, monkeypatch):
    """MULTI_AGENT_LEGACY_RULE_OPTIMIZER=1 → legacy loop used instead."""
    monkeypatch.setenv("MULTI_AGENT_LEGACY_RULE_OPTIMIZER", "1")
    orch = _make_orch(
        tmp_profile,
        critic_reviews=[{
            "agent_key": "dev", "verdict": "REVISE", "score": 5,
            "strengths": [], "weaknesses": ["x"],
            "checklist_flat": [], "checklist_answers": {},
        }],
        llm_sugs=[{
            "agent_key": "dev", "reason": "x",
            "addition": "Do Y.", "target_type": "rule",
            "rule_path": Path("/nowhere/dev.md"),
            "profile": tmp_profile, "source": "llm",
        }],
    )

    called = {"legacy": 0, "evolver": 0}
    monkeypatch.setattr(rule_runner, "run_rule_evolver",
                         lambda o, s, h: called.__setitem__("evolver", called["evolver"] + 1))
    monkeypatch.setattr(rule_runner, "run_legacy_rule_optimizer",
                         lambda o, s, h: called.__setitem__("legacy", called["legacy"] + 1))
    monkeypatch.setattr(rule_runner, "rollback_regressed_rules",
                         lambda o, h: None)
    monkeypatch.setattr(rule_runner, "maybe_upgrade_criteria",
                         lambda o, h: [])

    rule_runner.run_rule_optimizer(orch)

    assert called["legacy"] == 1
    assert called["evolver"] == 0


# ── rollback_regressed_rules ─────────────────────────────────────────────────

def test_rollback_regressed_rules_triggers_rollback(tmp_profile, tmp_path, monkeypatch):
    """An applied entry whose trend shows regression must trigger rollback +
    blacklist."""
    # Seed a tiny history file with an "applied but regressed" entry
    hist_path = tmp_path / "rules" / tmp_profile / ".revise_history.json"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps({
        "dev:rule:somepattern": {
            "agent_key": "dev",
            "target_type": "rule",
            "fingerprint": "somepattern",
            "reason_sample": "missing error handling",
            "addition_sample": "Wrap in try/except.",
            "count": 6,
            "applied": True,
            "applied_at": "2026-01-01T00:00:00",
            "apply_session_id": "S-applied",
            "backup_path": str(tmp_path / "fake-backup.md"),
        }
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp_path / "fake-backup.md").write_text("old rule", encoding="utf-8")
    history = ReviseHistory(hist_path)

    # Make detect_regression return True for our applied session
    monkeypatch.setattr(history, "detect_regression",
                         lambda agent_key, sid: sid == "S-applied")

    # Trigger the rollback path
    orch = _make_orch(tmp_profile)

    rule_lifecycle.rollback_regressed_rules(orch, history)

    # RuleOptimizer.rollback was invoked for the regressed pattern
    assert orch.rule_optimizer.rollback_calls == 1


def test_rollback_regressed_rules_noop_when_clean(tmp_profile, tmp_path):
    """If no applied entry is regressing, no rollback happens."""
    hist_path = tmp_path / "rules" / tmp_profile / ".revise_history.json"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text("{}", encoding="utf-8")
    history = ReviseHistory(hist_path)
    orch = _make_orch(tmp_profile)

    rule_lifecycle.rollback_regressed_rules(orch, history)

    assert orch.rule_optimizer.rollback_calls == 0


# ── Legacy classifier gate ───────────────────────────────────────────────────

def test_legacy_classifier_auto_mode_applies_when_decision_apply(
        tmp_profile, tmp_path, monkeypatch):
    """auto mode + classifier says 'apply' → rule_optimizer.apply() is called."""
    monkeypatch.setenv("MULTI_AGENT_LEARNING_MODE", "auto")

    hist_path = tmp_path / "rules" / tmp_profile / ".revise_history.json"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text("{}", encoding="utf-8")
    history = ReviseHistory(hist_path)

    # Fake the classifier + feature snapshot so no actual training runs
    import analyzer as _analyzer
    monkeypatch.setattr(_analyzer, "classifier_should_apply",
                         lambda **kw: {
                             "decision": "apply", "via": "classifier",
                             "proba": 0.12, "features": {},
                             "reason": "P(regress) low",
                         })
    monkeypatch.setattr(_analyzer, "snapshot_features",
                         lambda **kw: None)
    monkeypatch.setattr(_analyzer, "train_regression_classifier",
                         lambda profile, history: {"fitted": False})
    monkeypatch.setattr(_analyzer, "load_outcome_entries",
                         lambda profile: [])

    orch = _make_orch(tmp_profile)
    # Count must already be AUTO_THRESHOLD-ish, but legacy path passes
    # classifier decision through regardless. Just hand it one suggestion.
    suggestion = {
        "agent_key": "dev", "reason": "missing tests",
        "addition": "Every PR must include unit tests.",
        "target_type": "rule",
        "rule_path": tmp_path / "rules" / tmp_profile / "dev.md",
        "profile": tmp_profile, "source": "llm",
        "suggested_rule": "new rule body",
    }

    rule_lifecycle.run_legacy_rule_optimizer(orch, [suggestion], history)

    assert orch.rule_optimizer.apply_calls == 1


def test_legacy_classifier_auto_mode_skips_when_decision_shadow(
        tmp_profile, tmp_path, monkeypatch):
    """auto mode + classifier says 'shadow' → apply() NOT called."""
    monkeypatch.setenv("MULTI_AGENT_LEARNING_MODE", "auto")

    hist_path = tmp_path / "rules" / tmp_profile / ".revise_history.json"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text("{}", encoding="utf-8")
    history = ReviseHistory(hist_path)

    import analyzer as _analyzer
    monkeypatch.setattr(_analyzer, "classifier_should_apply",
                         lambda **kw: {
                             "decision": "shadow", "via": "classifier",
                             "proba": 0.40, "features": {},
                             "reason": "mid risk",
                         })
    monkeypatch.setattr(_analyzer, "snapshot_features", lambda **kw: None)
    monkeypatch.setattr(_analyzer, "train_regression_classifier",
                         lambda profile, history: {"fitted": False})
    monkeypatch.setattr(_analyzer, "load_outcome_entries",
                         lambda profile: [])

    orch = _make_orch(tmp_profile)
    suggestion = {
        "agent_key": "dev", "reason": "x",
        "addition": "Do Y.", "target_type": "rule",
        "rule_path": tmp_path / "rules" / tmp_profile / "dev.md",
        "profile": tmp_profile, "source": "llm",
        "suggested_rule": "body",
    }

    rule_lifecycle.run_legacy_rule_optimizer(orch, [suggestion], history)

    assert orch.rule_optimizer.apply_calls == 0


def test_legacy_learning_mode_off_records_but_doesnt_apply(
        tmp_profile, tmp_path, monkeypatch):
    """MULTI_AGENT_LEARNING_MODE=off → suggestions recorded, never applied."""
    monkeypatch.setenv("MULTI_AGENT_LEARNING_MODE", "off")

    hist_path = tmp_path / "rules" / tmp_profile / ".revise_history.json"
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text("{}", encoding="utf-8")
    history = ReviseHistory(hist_path)

    orch = _make_orch(tmp_profile)
    suggestion = {
        "agent_key": "dev", "reason": "x",
        "addition": "Do Y.", "target_type": "rule",
        "rule_path": tmp_path / "rules" / tmp_profile / "dev.md",
        "profile": tmp_profile, "source": "llm",
        "suggested_rule": "body",
    }

    rule_lifecycle.run_legacy_rule_optimizer(orch, [suggestion], history)

    assert orch.rule_optimizer.apply_calls == 0
    # Pattern was recorded in history (count should be ≥ 1)
    entries = [v for k, v in history._data.items()
               if isinstance(v, dict) and not k.startswith("__")]
    assert any(e.get("count", 0) >= 1 for e in entries)
