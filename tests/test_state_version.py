"""
Tests for core.state_version + its integration into ReviseHistory and
the regression_classifier model file.

Verifies:
  * stamp / detect_version / migrate_if_needed behave correctly on fresh
    files, pre-versioned files, and future versions with no migration.
  * ReviseHistory stamps the version on every save and reads it back.
  * `_schema_version` is transparently ignored by history iterators.
  * regression_classifier.load_model tolerates an extra schema key.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.state_version import (
    CURRENT_REVISE_HISTORY_VERSION, CURRENT_REGRESSION_MODEL_VERSION,
    SCHEMA_VERSION_KEY, stamp, detect_version, migrate_if_needed,
)
from learning.revise_history import ReviseHistory


# ── Pure-function behavior ───────────────────────────────────────────────────

def test_stamp_mutates_in_place_and_returns_dict():
    d = {"a": 1}
    out = stamp(d, 7)
    assert out is d
    assert d[SCHEMA_VERSION_KEY] == 7


def test_stamp_rejects_non_dict():
    with pytest.raises(TypeError):
        stamp(["not", "dict"], 1)   # type: ignore[arg-type]


def test_detect_version_defaults_to_zero_when_absent():
    assert detect_version({}) == 0
    assert detect_version({"foo": "bar"}) == 0
    assert detect_version({SCHEMA_VERSION_KEY: 3}) == 3


def test_detect_version_handles_non_int_gracefully():
    assert detect_version({SCHEMA_VERSION_KEY: "nope"}) == 0
    assert detect_version({SCHEMA_VERSION_KEY: None}) == 0


def test_migrate_if_needed_idempotent_at_current_version():
    d = {"x": 1, SCHEMA_VERSION_KEY: 1}
    out = migrate_if_needed(d, 1, 1, schema="revise_history")
    assert out == {"x": 1, SCHEMA_VERSION_KEY: 1}


def test_migrate_if_needed_from_zero_stamps_to_current():
    """Pre-versioned file (no `_schema_version` key) should be stamped
    at current version without any migration function running."""
    d = {"some_key": "some_value"}
    out = migrate_if_needed(d, 0, 2, schema="revise_history")
    assert out[SCHEMA_VERSION_KEY] == 2
    assert out["some_key"] == "some_value"


def test_migrate_if_needed_missing_migration_returns_best_effort():
    """If someone asks to migrate 1 → 2 but no migration is registered,
    we get the data back stamped at version we could reach (1)."""
    d = {"x": 1, SCHEMA_VERSION_KEY: 1}
    out = migrate_if_needed(d, 1, 2, schema="revise_history")
    # No migration registered → returned stamped at the starting version.
    assert out[SCHEMA_VERSION_KEY] == 1
    assert out["x"] == 1


# ── ReviseHistory integration ────────────────────────────────────────────────

def test_revise_history_stamps_version_on_save(tmp_path):
    """A fresh write must include the schema-version key on disk."""
    hist_path = tmp_path / ".revise_history.json"
    h = ReviseHistory(hist_path)
    h.record("dev", "some reason", "some addition", "rule")

    raw = json.loads(hist_path.read_text(encoding="utf-8"))
    assert raw[SCHEMA_VERSION_KEY] == CURRENT_REVISE_HISTORY_VERSION


def test_revise_history_reads_unversioned_file_cleanly(tmp_path):
    """An old on-disk file without `_schema_version` must load + get
    stamped with current version on first save."""
    from learning.revise_history import _fingerprint
    seed_reason = "missing error handling"
    seed_key = f"dev:rule:{_fingerprint(seed_reason)}"

    hist_path = tmp_path / ".revise_history.json"
    hist_path.write_text(json.dumps({
        seed_key: {
            "agent_key": "dev", "target_type": "rule",
            "fingerprint": _fingerprint(seed_reason),
            "reason_sample": seed_reason, "addition_sample": "Wrap in try/except.",
            "count": 3,
        }
    }), encoding="utf-8")

    h = ReviseHistory(hist_path)
    # Pre-existing pattern is still accessible — version stamp is transparent.
    assert h.get_count("dev", seed_reason, "rule") == 3

    # Force a write; file now has schema version.
    h.record("dev", "new_pattern", "new_addition", "rule")
    raw = json.loads(hist_path.read_text(encoding="utf-8"))
    assert raw[SCHEMA_VERSION_KEY] == CURRENT_REVISE_HISTORY_VERSION


def test_revise_history_iterators_skip_schema_version_key(tmp_path):
    """`_schema_version` is an int, so filters like `isinstance(e, dict)`
    in get_applied_entries / chronic_patterns should naturally skip it."""
    hist_path = tmp_path / ".revise_history.json"
    h = ReviseHistory(hist_path)
    # Seed one applied entry so get_applied_entries has something to find.
    h.record("dev", "pattern A", "addition A", "rule")
    h.mark_applied("dev", "pattern A", "rule",
                    backup_path="/tmp/backup.md",
                    apply_session_id="S-1")

    applied = h.get_applied_entries()
    assert len(applied) == 1

    # Verify no entry looks like the schema version sentinel
    for e in applied:
        assert e.get("agent_key") is not None   # dict entries only
        assert SCHEMA_VERSION_KEY not in e.get("_key", "")


# ── regression_classifier integration ────────────────────────────────────────

def test_regression_classifier_load_model_tolerates_versioned_file(tmp_path, monkeypatch):
    """`load_model` must accept a model.json that carries `_schema_version`
    and still hydrate a LogisticModel from it."""
    from analyzer import regression_classifier as rc

    # Redirect the profile's learning dir to tmp.
    monkeypatch.setattr(rc, "learning_dir",
                         lambda p, tmp=tmp_path: tmp / "rules" / p / ".learning")
    (tmp_path / "rules" / "testprof" / ".learning").mkdir(parents=True, exist_ok=True)
    model_path = tmp_path / "rules" / "testprof" / ".learning" / "regression_model.json"

    payload = {
        SCHEMA_VERSION_KEY: CURRENT_REGRESSION_MODEL_VERSION,
        "l2": 0.1, "lr": 0.05, "epochs": 500,
        "weights": [0.0] * 8, "bias": 0.0,
        "means":   [0.0] * 8, "stds":  [1.0] * 8,
        "feature_keys": list(rc.FEATURE_KEYS),
    }
    model_path.write_text(json.dumps(payload), encoding="utf-8")

    model = rc.load_model("testprof")
    assert model is not None
    assert len(model.weights) == 8


def test_regression_classifier_load_model_returns_none_on_corrupt(tmp_path, monkeypatch):
    """Corrupt JSON must return None, not crash."""
    from analyzer import regression_classifier as rc

    monkeypatch.setattr(rc, "learning_dir",
                         lambda p, tmp=tmp_path: tmp / "rules" / p / ".learning")
    (tmp_path / "rules" / "testprof" / ".learning").mkdir(parents=True, exist_ok=True)
    model_path = tmp_path / "rules" / "testprof" / ".learning" / "regression_model.json"
    model_path.write_text("not valid json{{{", encoding="utf-8")

    assert rc.load_model("testprof") is None
