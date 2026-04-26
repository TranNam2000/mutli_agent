"""Tests for analyzer.regression_classifier — logistic regression + gate."""
from analyzer.regression_classifier import (
    LogisticModel, FEATURE_KEYS, MIN_TRAINING_SAMPLES,
    APPLY_MAX_PROBA, SHADOW_MAX_PROBA, FALLBACK_COUNT_THRESHOLD,
    _sigmoid,
)


def test_sigmoid_stable_at_extremes():
    # Must not overflow for large |z|.
    assert 0 < _sigmoid(500) <= 1
    assert 0 <= _sigmoid(-500) < 1
    assert _sigmoid(0) == 0.5


def test_logistic_model_separates_synthetic_classes():
    """Label=1 when pattern_count is high; label=0 when low."""
    # Use the exact FEATURE_KEYS order.
    X = []
    y = []
    # 20 positive (high count → regress)
    for i in range(20):
        X.append([8 + i % 3, 200, 1, 4, 2, 1.3, 10, 3000])
        y.append(1)
    # 20 negative (low count → OK)
    for i in range(20):
        X.append([2 + i % 2, 100, 1, 8, 0, 1.0, 5, 2000])
        y.append(0)

    m = LogisticModel(epochs=800)
    stats = m.fit(X, y, list(FEATURE_KEYS))
    assert stats["n"] == 40
    assert stats["train_accuracy"] >= 0.85

    # High pattern_count → high P(regress)
    p_high = m.predict_proba({
        "pattern_count": 9, "rule_length": 200, "source_count": 1,
        "avg_critic_recent": 4, "avg_missing_info_recent": 2,
        "avg_cost_ratio_recent": 1.3, "sessions_since_last_apply": 10,
        "current_rule_size": 3000,
    })
    # Low pattern_count → low P(regress)
    p_low = m.predict_proba({
        "pattern_count": 2, "rule_length": 100, "source_count": 1,
        "avg_critic_recent": 8, "avg_missing_info_recent": 0,
        "avg_cost_ratio_recent": 1.0, "sessions_since_last_apply": 5,
        "current_rule_size": 2000,
    })
    assert p_high > p_low
    assert p_high > 0.5
    assert p_low < 0.5


def test_model_roundtrip_to_dict():
    m = LogisticModel()
    m.fit(
        [[1, 1, 1, 1, 1, 1, 1, 1]] * 5 + [[2, 2, 2, 2, 2, 2, 2, 2]] * 5,
        [0] * 5 + [1] * 5,
        list(FEATURE_KEYS),
    )
    d = m.to_dict()
    m2 = LogisticModel.from_dict(d)
    # Same features → same prediction.
    features = {k: 1.5 for k in FEATURE_KEYS}
    assert abs(m.predict_proba(features) - m2.predict_proba(features)) < 1e-9


def test_thresholds_order():
    """Sanity: apply < shadow < 1.0 and fallback count > 1."""
    assert 0 < APPLY_MAX_PROBA < SHADOW_MAX_PROBA < 1.0
    assert MIN_TRAINING_SAMPLES >= 10
    assert FALLBACK_COUNT_THRESHOLD >= 1
