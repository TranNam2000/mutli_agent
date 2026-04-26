"""Tests for analyzer.outcome_logger — append-only JSONL + correlation."""
from analyzer import outcome_logger


def test_log_empty_reviews_is_noop(tmp_profile):
    assert outcome_logger.log_session_outcomes(tmp_profile, "s1", []) == 0


def test_one_entry_per_agent_not_per_revise_round(tmp_profile, fake_reviews):
    # Duplicate reviews for BA across rounds — only the last should be kept.
    rounds = [
        {**fake_reviews[0], "round": 1, "score": 5},
        {**fake_reviews[0], "round": 2, "score": 7},  # latest
    ]
    n = outcome_logger.log_session_outcomes(tmp_profile, "s1", rounds)
    assert n == 1  # one per agent_key
    entries = outcome_logger.load_entries(tmp_profile)
    assert len(entries) == 1
    assert entries[0]["final"] == 7   # latest round kept


def test_signals_recorded(tmp_profile, fake_reviews):
    outcome_logger.log_session_outcomes(
        tmp_profile, "s1", fake_reviews,
        test_pass_rate=0.8,
        missing_info_by_agent={"dev": 2},
        clarif_count_by_agent={"ba": 3},
        cost_ratio_by_agent={"dev": 1.4},
    )
    entries = outcome_logger.load_entries(tmp_profile)
    dev = next(e for e in entries if e["agent_key"] == "dev")
    assert dev["signals"]["test_pass_rate"] == 0.8
    assert dev["signals"]["missing_info"] == 2
    assert dev["signals"]["cost_ratio"] == 1.4
    ba = next(e for e in entries if e["agent_key"] == "ba")
    assert ba["signals"]["clarif_count"] == 3


def test_append_across_sessions(tmp_profile, fake_reviews):
    outcome_logger.log_session_outcomes(tmp_profile, "s1", fake_reviews)
    outcome_logger.log_session_outcomes(tmp_profile, "s2", fake_reviews)
    entries = outcome_logger.load_entries(tmp_profile)
    sessions = {e["session_id"] for e in entries}
    assert sessions == {"s1", "s2"}


def test_pearson_requires_three_samples(tmp_profile, fake_reviews):
    # Only 2 sessions → "insufficient samples"
    outcome_logger.log_session_outcomes(tmp_profile, "s1", fake_reviews, test_pass_rate=0.9)
    outcome_logger.log_session_outcomes(tmp_profile, "s2", fake_reviews, test_pass_rate=0.7)
    rep = outcome_logger.correlation_report(tmp_profile)
    assert rep["by_agent"]["dev"].get("note", "").startswith("insufficient")


def test_pearson_positive_correlation_detectable(tmp_profile):
    """critic_raw increases ↔ test_pass_rate increases → r > 0."""
    for i in range(10):
        score = 5 + i // 2
        pass_rate = 0.5 + i * 0.05
        outcome_logger.log_session_outcomes(
            tmp_profile, f"s{i}",
            [{"agent_key": "dev", "score": score, "round": 1}],
            test_pass_rate=pass_rate,
        )
    rep = outcome_logger.correlation_report(tmp_profile)
    r = rep["by_agent"]["dev"]["pearson_vs_critic_raw"]["test_pass_rate"]
    assert r > 0.8, f"expected strong positive correlation, got {r}"
