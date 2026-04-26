"""
Regression classifier — predict whether applying a rule change will cause
score regression in upcoming sessions.

Why
---
The old gate (`ReviseHistory.should_auto_apply`) fires purely on count ≥ 5.
That ignores *context*: some patterns with 5 occurrences consistently lead
to regression when applied, others with only 3 occurrences are safe. This
module trains a logistic regression on historical apply outcomes so the
gate becomes context-aware.

Data pipeline
-------------
    1. Every rule apply (via ReviseHistory.mark_applied) snapshots features
       into `apply_features.jsonl`.
    2. After enough post-apply sessions, `detect_regression()` labels each
       past apply 0 / 1.
    3. `train()` fits a logistic regression on those (features, label) pairs.
    4. `predict_proba(features)` returns P(regression) for a new suggestion.

Gate logic (via `should_apply`)
-------------------------------
    < MIN_TRAINING_SAMPLES training examples → fall back to count threshold.
    proba < 0.20                             → "apply"
    proba in [0.20, 0.50)                    → "shadow" (A/B test)
    proba ≥ 0.50                             → "skip"

Pure Python (no numpy / sklearn) — ~40 training samples converge in
< 200ms. When the dataset grows beyond a few hundred rows, a user can swap
in sklearn if they want; the interface is compatible.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Iterable

from core.io_utils import atomic_write_text
from core.paths import learning_dir
from core.state_version import (
    CURRENT_REGRESSION_MODEL_VERSION, stamp, detect_version, migrate_if_needed,
)


# ── Thresholds ───────────────────────────────────────────────────────────────

MIN_TRAINING_SAMPLES = 30       # below this → threshold fallback
APPLY_MAX_PROBA      = 0.20     # P(regress) < this → auto-apply
SHADOW_MAX_PROBA     = 0.50     # P(regress) < this → shadow A/B

# Legacy fallback — mirrors the old should_auto_apply() behaviour.
FALLBACK_COUNT_THRESHOLD = 5

# Features we extract — order matters (indexes into the weight vector).
FEATURE_KEYS = (
    "pattern_count",
    "rule_length",
    "source_count",
    "avg_critic_recent",
    "avg_missing_info_recent",
    "avg_cost_ratio_recent",
    "sessions_since_last_apply",
    "current_rule_size",
)


def _features_path(profile: str) -> Path:
    return learning_dir(profile) / "apply_features.jsonl"


def _model_path(profile: str) -> Path:
    return learning_dir(profile) / "regression_model.json"


# ── Feature snapshot (write-side, at apply time) ─────────────────────────────

_write_lock = threading.Lock()


def snapshot_features(
    profile: str,
    *,
    apply_id: str,
    session_id: str,
    agent_key: str,
    features: dict,
) -> None:
    """Append a feature row at the moment we decide to apply a suggestion.
    The `regressed` label is filled in later by `backfill_labels`."""
    path = _features_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "apply_id":     apply_id,
        "session_id":   session_id,
        "agent_key":    agent_key,
        "features":     {k: features.get(k, 0.0) for k in FEATURE_KEYS},
        "regressed":    None,   # filled post-hoc
    }
    with _write_lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_feature_entries(profile: str) -> list[dict]:
    path = _features_path(profile)
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _save_feature_entries(profile: str, entries: list[dict]) -> None:
    atomic_write_text(
        _features_path(profile),
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
    )


def backfill_labels(profile: str, history) -> int:
    """
    Walk all feature rows with regressed=None. For each, ask the provided
    ReviseHistory whether that apply has since regressed. Returns number of
    rows newly labelled.
    """
    entries = _load_feature_entries(profile)
    if not entries:
        return 0
    updated = 0
    for e in entries:
        if e.get("regressed") is not None:
            continue
        apply_sid = e.get("session_id", "")
        agent_key = e.get("agent_key", "")
        if not (apply_sid and agent_key):
            continue
        try:
            if history.detect_regression(agent_key, apply_sid):
                e["regressed"] = 1
                updated += 1
            else:
                # Only mark 0 if we have enough post-apply data to trust it;
                # detect_regression returns False for both "definitely OK"
                # and "not enough data yet" — distinguish via score count.
                scores = history._data.get(f"__trend__{agent_key}", {}).get("scores", [])
                pivot = next((i for i, s in enumerate(scores)
                              if s["session_id"] == apply_sid), None)
                if pivot is not None and len(scores[pivot + 1:]) >= 2:
                    e["regressed"] = 0
                    updated += 1
        except (KeyError, AttributeError, IndexError, TypeError):
            continue
    if updated:
        _save_feature_entries(profile, entries)
    return updated


# ── Feature extraction (read-side, at decision time) ─────────────────────────

def build_features(
    *,
    agent_key: str,
    suggestion: dict,
    history,
    outcome_entries: Iterable[dict] | None = None,
) -> dict:
    """Compute the feature dict used by both snapshot and prediction."""
    # From the suggestion itself / history
    pattern_count = int(suggestion.get("count", 0)) or \
                    history.get_count(agent_key, suggestion.get("reason", ""),
                                       suggestion.get("target_type", "rule"))
    rule_length   = len(suggestion.get("addition", "") or "")
    sources       = suggestion.get("sources") or []
    source_count  = len(sources) if isinstance(sources, list) else 1

    # From outcome_logger history: look at last 5 entries for this agent
    oe = [e for e in (outcome_entries or [])
          if e.get("agent_key") == agent_key]
    oe = oe[-5:]  # most recent 5
    if oe:
        critic_vals = [float(e.get("critic_raw") or 0) for e in oe
                       if e.get("critic_raw") is not None]
        missing_vals = [float(e.get("signals", {}).get("missing_info") or 0)
                         for e in oe]
        cost_vals    = [float(e.get("signals", {}).get("cost_ratio") or 0)
                         for e in oe if e.get("signals", {}).get("cost_ratio")]
        avg_critic   = sum(critic_vals) / len(critic_vals) if critic_vals else 5.0
        avg_missing  = sum(missing_vals) / len(missing_vals) if missing_vals else 0.0
        avg_cost     = sum(cost_vals) / len(cost_vals) if cost_vals else 1.0
    else:
        avg_critic   = 5.0
        avg_missing  = 0.0
        avg_cost     = 1.0

    # Sessions since last apply for this agent (from ReviseHistory)
    applied = [e for e in history.get_applied_entries()
               if e.get("agent_key") == agent_key]
    sessions_since = 99 if not applied else 0  # simple proxy — exact count is harder

    # Current rule file size (approximate)
    try:
        from agents.base_agent import _RULES_DIR
        rule_file = _RULES_DIR / history.path.parent.name / f"{agent_key}.md"
        current_rule_size = len(rule_file.read_text(encoding="utf-8")) \
                            if rule_file.exists() else 0
    except (OSError, UnicodeDecodeError, ImportError):
        current_rule_size = 0

    return {
        "pattern_count":             float(pattern_count),
        "rule_length":               float(rule_length),
        "source_count":              float(source_count),
        "avg_critic_recent":         float(avg_critic),
        "avg_missing_info_recent":   float(avg_missing),
        "avg_cost_ratio_recent":     float(avg_cost),
        "sessions_since_last_apply": float(sessions_since),
        "current_rule_size":         float(current_rule_size),
    }


# ── Pure-Python logistic regression ──────────────────────────────────────────

def _sigmoid(z: float) -> float:
    # Numerically stable
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _standardise(X: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    """Z-score normalise each column. Returns (normalised_X, means, stds)."""
    n = len(X)
    d = len(X[0])
    means = [sum(row[j] for row in X) / n for j in range(d)]
    stds = [
        math.sqrt(sum((row[j] - means[j]) ** 2 for row in X) / n) or 1.0
        for j in range(d)
    ]
    X_norm = [[(row[j] - means[j]) / stds[j] for j in range(d)] for row in X]
    return X_norm, means, stds


class LogisticModel:
    """Minimal logistic regression with L2 regularisation, fit by gradient
    descent. Kept pure-Python so the pipeline has no numpy dependency."""

    def __init__(self, l2: float = 0.1, lr: float = 0.05, epochs: int = 500):
        self.l2 = l2
        self.lr = lr
        self.epochs = epochs
        self.weights: list[float] = []
        self.bias: float = 0.0
        self.means: list[float] = []
        self.stds: list[float] = []
        self.feature_keys: list[str] = []

    def fit(self, X: list[list[float]], y: list[int],
            feature_keys: list[str]) -> dict:
        self.feature_keys = list(feature_keys)
        if not X:
            raise ValueError("no training data")
        X_norm, self.means, self.stds = _standardise(X)
        d = len(X_norm[0])
        n = len(X_norm)

        self.weights = [0.0] * d
        self.bias = 0.0

        for _ in range(self.epochs):
            # Compute gradients
            grad_w = [0.0] * d
            grad_b = 0.0
            for i in range(n):
                z = self.bias + sum(self.weights[j] * X_norm[i][j] for j in range(d))
                p = _sigmoid(z)
                err = p - y[i]
                for j in range(d):
                    grad_w[j] += err * X_norm[i][j]
                grad_b += err
            # Update with L2 reg on weights (not bias)
            for j in range(d):
                self.weights[j] -= self.lr * (grad_w[j] / n + self.l2 * self.weights[j])
            self.bias -= self.lr * (grad_b / n)

        # Training accuracy — quick sanity signal
        correct = 0
        for i in range(n):
            p = self._predict_one(X_norm[i])
            if (p >= 0.5) == bool(y[i]):
                correct += 1
        return {"n": n, "train_accuracy": correct / n if n else 0.0}

    def _predict_one(self, x_norm: list[float]) -> float:
        z = self.bias + sum(self.weights[j] * x_norm[j] for j in range(len(x_norm)))
        return _sigmoid(z)

    def predict_proba(self, features: dict) -> float:
        """Return P(regression=1) for a feature dict."""
        if not self.weights:
            raise RuntimeError("model not fitted")
        x = [features.get(k, 0.0) for k in self.feature_keys]
        x_norm = [
            (x[j] - self.means[j]) / (self.stds[j] or 1.0)
            for j in range(len(x))
        ]
        return self._predict_one(x_norm)

    def to_dict(self) -> dict:
        return {
            "l2": self.l2, "lr": self.lr, "epochs": self.epochs,
            "weights": self.weights, "bias": self.bias,
            "means": self.means, "stds": self.stds,
            "feature_keys": self.feature_keys,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogisticModel":
        m = cls(l2=d.get("l2", 0.1), lr=d.get("lr", 0.05),
                epochs=d.get("epochs", 500))
        m.weights = list(d.get("weights", []))
        m.bias = float(d.get("bias", 0.0))
        m.means = list(d.get("means", []))
        m.stds = list(d.get("stds", []))
        m.feature_keys = list(d.get("feature_keys", []))
        return m


# ── Public API: train, load, decide ──────────────────────────────────────────

def train(profile: str, history=None) -> dict:
    """
    Backfill labels, collect training data, fit model, persist to disk.
    Returns metadata: {"n": N, "train_accuracy": 0.xx, "fitted": bool, ...}.
    """
    if history is not None:
        backfill_labels(profile, history)

    entries = [e for e in _load_feature_entries(profile)
               if e.get("regressed") in (0, 1)]
    n = len(entries)
    meta = {"n": n, "fitted": False}
    if n < MIN_TRAINING_SAMPLES:
        meta["reason"] = f"insufficient samples ({n} < {MIN_TRAINING_SAMPLES})"
        return meta

    X = [[float(e["features"].get(k, 0.0)) for k in FEATURE_KEYS] for e in entries]
    y = [int(e["regressed"]) for e in entries]

    model = LogisticModel()
    stats = model.fit(X, y, list(FEATURE_KEYS))
    payload = stamp(model.to_dict(), CURRENT_REGRESSION_MODEL_VERSION)
    atomic_write_text(
        _model_path(profile),
        json.dumps(payload, indent=2, ensure_ascii=False),
    )
    meta.update({"fitted": True, **stats})
    return meta


def load_model(profile: str) -> LogisticModel | None:
    path = _model_path(profile)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    version = detect_version(raw)
    raw = migrate_if_needed(
        raw, version, CURRENT_REGRESSION_MODEL_VERSION,
        schema="regression_model",
    )
    try:
        return LogisticModel.from_dict(raw)
    except (KeyError, ValueError, TypeError):
        return None


def should_apply(
    *,
    profile: str,
    agent_key: str,
    suggestion: dict,
    history,
    outcome_entries: Iterable[dict] | None = None,
) -> dict:
    """
    Gate decision for one rule suggestion.

    Returns
    -------
    {
        "decision": "apply" | "shadow" | "skip" | "hold",
        "proba":     float | None,       # P(regression)
        "reason":    str,                 # human-readable
        "via":       "classifier" | "threshold"
    }

    "hold" = neither apply nor shadow nor skip yet; user should review.
    """
    model = load_model(profile)
    features = build_features(
        agent_key=agent_key, suggestion=suggestion,
        history=history, outcome_entries=outcome_entries,
    )

    # Fallback when no model OR insufficient data
    if model is None:
        count = int(features.get("pattern_count", 0))
        if count >= FALLBACK_COUNT_THRESHOLD:
            return {"decision": "hold", "proba": None,
                    "reason": f"no model yet, count={count} ≥ {FALLBACK_COUNT_THRESHOLD} — user should review",
                    "via": "threshold", "features": features}
        return {"decision": "skip", "proba": None,
                "reason": f"no model yet, count={count} < {FALLBACK_COUNT_THRESHOLD}",
                "via": "threshold", "features": features}

    proba = model.predict_proba(features)
    if proba < APPLY_MAX_PROBA:
        decision = "apply"
        reason = f"P(regress)={proba:.2f} < {APPLY_MAX_PROBA} — safe to auto-apply"
    elif proba < SHADOW_MAX_PROBA:
        decision = "shadow"
        reason = f"P(regress)={proba:.2f} — uncertain, shadow A/B test"
    else:
        decision = "skip"
        reason = f"P(regress)={proba:.2f} ≥ {SHADOW_MAX_PROBA} — likely harmful, skip"
    return {"decision": decision, "proba": proba, "reason": reason,
            "via": "classifier", "features": features}


# ── CLI helper ───────────────────────────────────────────────────────────────

def format_status(profile: str, history=None) -> str:
    """Human-friendly summary for `mag rubric-classifier`."""
    lines = [f"\n  🤖 REGRESSION CLASSIFIER — profile: {profile}"]
    lines.append(f"  {'═'*60}")

    entries_all = _load_feature_entries(profile)
    labelled = [e for e in entries_all if e.get("regressed") in (0, 1)]
    n_pos = sum(1 for e in labelled if e.get("regressed") == 1)
    n_neg = len(labelled) - n_pos

    lines.append(f"  Snapshots: {len(entries_all)} total | "
                 f"labelled: {len(labelled)} ({n_pos} regressed, {n_neg} OK)")

    if len(labelled) < MIN_TRAINING_SAMPLES:
        lines.append(f"  Status: insufficient data (need {MIN_TRAINING_SAMPLES}, have {len(labelled)})")
        lines.append(f"  Fallback: count-threshold gate (≥ {FALLBACK_COUNT_THRESHOLD} occurrences)")
        return "\n".join(lines)

    model = load_model(profile)
    if model is None:
        lines.append(f"  Status: have data but no model — run `train()`")
        return "\n".join(lines)

    lines.append(f"  Status: model trained, weights (standardised features):")
    for key, w in zip(model.feature_keys, model.weights):
        sign = "▲" if w > 0 else "▼"
        lines.append(f"    {sign} {key:30} {w:+.3f}")
    lines.append(f"  Bias: {model.bias:+.3f}")
    lines.append(f"  Gate: apply<{APPLY_MAX_PROBA:.2f}  shadow<{SHADOW_MAX_PROBA:.2f}  else skip")
    return "\n".join(lines)
