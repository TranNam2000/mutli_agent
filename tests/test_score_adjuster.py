"""
Tests for analyzer.score_adjuster — the blended-weighted-sum logic that
replaces the old compound-subtraction bug. Key invariants:

  1. No compound subtraction: a single 8-score never crashes to 1 from
     two moderate penalties.
  2. Upstream attribution: when Dev output has MISSING_INFO pointing at
     BA/TL, at least part of the test-failure pressure moves to the source.
  3. Missing-info on the dev output reduces Dev's share (≥ 30% minimum).
"""
from types import SimpleNamespace

from analyzer.score_adjuster import (
    ScoreAdjuster, ScoreAdjustment, count_missing_info,
)


def _patrol(passed: int, failed: int):
    """Build a fake PatrolSuiteResult-like object."""
    return SimpleNamespace(
        android=SimpleNamespace(passed=passed, failed=failed),
        ios=None,
    )


def test_no_compound_subtraction_on_moderate_failure():
    """Old bug: 8 − 3 − 2 = 3, then more penalties → 1. New blend stays ≥ 3."""
    adj = ScoreAdjuster()
    reviews = [{"agent_key": "dev", "agent_role": "Developer", "score": 8, "round": 1}]
    adj.apply_test_outcomes(reviews, patrol_result=_patrol(3, 7))  # 30% pass
    assert reviews[0]["score"] >= 3


def test_zero_pressure_leaves_scores_untouched():
    adj = ScoreAdjuster()
    reviews = [{"agent_key": "dev", "score": 8, "round": 1}]
    # No patrol, no maestro → no adjustment
    adj.apply_test_outcomes(reviews, patrol_result=None, maestro_result=None)
    assert reviews[0]["score"] == 8
    assert "score_original" not in reviews[0]


def test_upstream_attribution_moves_penalty_to_source():
    """Dev MISSING_INFO pointing at BA → BA takes some of the pressure."""
    adj = ScoreAdjuster()
    reviews = [
        {"agent_key": "dev", "agent_role": "Developer", "score": 8, "round": 1},
        {"agent_key": "ba",  "agent_role": "BA",        "score": 8, "round": 1},
    ]
    adj.apply_test_outcomes(
        reviews,
        patrol_result=_patrol(0, 10),   # 100% fail — strong signal
        missing_info_attribution={"ba": 3},  # Dev flagged 3 MISSING_INFO → BA
    )
    ba_final = next(r["score"] for r in reviews if r["agent_key"] == "ba")
    # BA is not fully exonerated — takes some penalty.
    assert ba_final < 8, f"BA should be penalised, got {ba_final}"


def test_count_missing_info_maps_source_correctly():
    txt = (
        "MISSING_INFO: OAuth timeout — MUST_ASK: TechLead\n"
        "MISSING_INFO: rate limit — MUST_ASK: BA\n"
        "MISSING_INFO: UI state — MUST_ASK: Design\n"
        "MISSING_INFO: orphan — (no source)\n"
    )
    got = count_missing_info(txt)
    assert got.get("techlead") == 1
    assert got.get("ba") == 1
    assert got.get("design") == 1


def test_score_adjustment_summary_shape():
    a = ScoreAdjustment(original=8, adjusted=5)
    a.penalties.append(("Patrol fail", 2.5))
    a.bonuses.append(("Early delivery", 0.5))
    s = a.summary()
    assert "8/10" in s and "5/10" in s
    assert "Patrol fail" in s
    assert "Early delivery" in s
