"""
Tests for the `mag shadow-status` dashboard command.

Verifies:
  * empty state (no log file) → friendly message, no crash
  * registered variant with sub-threshold samples → "waiting"
  * variant with enough samples + strong positive delta → PROMOTE verdict
  * variant with enough samples + strong negative delta → DEMOTE verdict
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_shadow_status_empty(tmp_path):
    """No shadow_log.json file → friendly empty-state message."""
    from cli.ux import shadow_status_report

    (tmp_path / "testprof").mkdir(parents=True, exist_ok=True)
    out = shadow_status_report("testprof", rules_dir=tmp_path)

    assert "Shadow A/B status" in out
    assert "No shadow log yet" in out
    # Must not crash and must not claim variants exist
    assert "promote" not in out.lower() or "no shadow log" in out.lower()


def test_shadow_status_registered_but_insufficient_samples(tmp_path):
    """A variant with only 1 baseline + 1 shadow sample → verdict line says
    waiting (below SHADOW_MIN_SESSIONS)."""
    from cli.ux import shadow_status_report

    profile_dir = tmp_path / "testprof"
    profile_dir.mkdir(parents=True, exist_ok=True)
    log_path = profile_dir / ".shadow_log.json"
    log_path.write_text(json.dumps({
        "variants": {
            "dev:rule": {
                "agent_key": "dev", "target_type": "rule",
                "shadow_path":  str(profile_dir / "dev.shadow.md"),
                "suggestion": "add error handling",
                "created_at": "2026-01-01T00:00:00",
                "baseline": [{"session_id": "S1", "score": 7.0, "ts": "2026-01-01T00:00:00"}],
                "shadow":   [{"session_id": "S1", "score": 8.0, "ts": "2026-01-01T00:00:00"}],
            }
        }
    }, ensure_ascii=False), encoding="utf-8")

    out = shadow_status_report("testprof", rules_dir=tmp_path)

    assert "dev:rule" in out
    assert "baseline n=1" in out
    assert "shadow n=1" in out
    assert "waiting" in out


def test_shadow_status_promote_verdict_on_positive_delta(tmp_path):
    """≥10 samples each side, shadow consistently +2 higher → PROMOTE."""
    from cli.ux import shadow_status_report

    profile_dir = tmp_path / "testprof"
    profile_dir.mkdir(parents=True, exist_ok=True)
    log_path = profile_dir / ".shadow_log.json"

    # Stable scores so stddev stays small (below SHADOW_MAX_STDDEV)
    baseline_scores = [7.0, 7.1, 7.0, 7.2, 7.0, 7.1, 7.0, 7.1, 7.0, 7.1]
    shadow_scores   = [8.5, 8.4, 8.5, 8.6, 8.5, 8.4, 8.5, 8.6, 8.5, 8.4]

    log_path.write_text(json.dumps({
        "variants": {
            "dev:rule": {
                "agent_key": "dev", "target_type": "rule",
                "shadow_path": str(profile_dir / "dev.shadow.md"),
                "suggestion": "add error handling",
                "created_at": "2026-01-01T00:00:00",
                "baseline": [{"session_id": f"S-b{i}", "score": s,
                              "ts": "2026-01-01"}
                             for i, s in enumerate(baseline_scores)],
                "shadow":   [{"session_id": f"S-s{i}", "score": s,
                              "ts": "2026-01-01"}
                             for i, s in enumerate(shadow_scores)],
            }
        }
    }, ensure_ascii=False), encoding="utf-8")

    out = shadow_status_report("testprof", rules_dir=tmp_path)

    assert "dev:rule" in out
    assert "PROMOTE" in out
    assert "+1." in out or "+1," in out   # delta ≈ +1.4-1.5


def test_shadow_status_demote_verdict_on_negative_delta(tmp_path):
    """Shadow consistently worse → DEMOTE verdict."""
    from cli.ux import shadow_status_report

    profile_dir = tmp_path / "testprof"
    profile_dir.mkdir(parents=True, exist_ok=True)
    log_path = profile_dir / ".shadow_log.json"

    baseline_scores = [8.0, 8.1, 8.0, 8.2, 8.0, 8.1, 8.0, 8.1, 8.0, 8.1]
    shadow_scores   = [6.5, 6.4, 6.5, 6.6, 6.5, 6.4, 6.5, 6.6, 6.5, 6.4]

    log_path.write_text(json.dumps({
        "variants": {
            "ba:rule": {
                "agent_key": "ba", "target_type": "rule",
                "shadow_path": str(profile_dir / "ba.shadow.md"),
                "suggestion": "reject", "created_at": "2026-01-01",
                "baseline": [{"session_id": f"S-b{i}", "score": s, "ts": "x"}
                             for i, s in enumerate(baseline_scores)],
                "shadow":   [{"session_id": f"S-s{i}", "score": s, "ts": "x"}
                             for i, s in enumerate(shadow_scores)],
            }
        }
    }, ensure_ascii=False), encoding="utf-8")

    out = shadow_status_report("testprof", rules_dir=tmp_path)

    assert "ba:rule" in out
    assert "DEMOTE" in out
