"""
Outcome logger — append per-agent (score, real_outcome) tuples after every
session so we can compute correlation and tell whether the critic rubric
actually predicts runtime quality.

Storage
-------
    rules/<profile>/.learning/score_correlation.jsonl

Each line is a JSON object::

    {
      "session_id": "20260422_144550_388d",
      "timestamp":  "2026-04-22T14:51:07",
      "agent_key":  "dev",
      "critic_raw": 8,                 # score from Critic before adjustments
      "final":      5,                 # score after ScoreAdjuster
      "signals": {
        "test_pass_rate":    0.70,     # 0..1 or null nếu không tests ran
        "missing_info":      2,        # MISSING_INFO leaked downstream
        "clarif_count":      3,        # downstream clarification requests
        "cost_ratio":        1.8,      # used / expected_budget
        "user_feedback":     null      # 1-5 star if user filed feedback, else null
      }
    }

The file is append-only JSONL so parallel sessions don't step on each other
(we open with line-buffered write + newline). Read-side scans all lines and
correlates `critic_raw` vs each signal.
"""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from core.paths import learning_dir


_write_lock = threading.Lock()


def _log_path(profile: str) -> Path:
    return learning_dir(profile) / "score_correlation.jsonl"


def log_session_outcomes(
    profile: str,
    session_id: str,
    reviews: Iterable[dict],
    *,
    test_pass_rate: float | None = None,
    missing_info_by_agent: dict[str, int] | None = None,
    clarif_count_by_agent: dict[str, int] | None = None,
    cost_ratio_by_agent: dict[str, float] | None = None,
    user_feedback: dict[str, int] | None = None,
) -> int:
    """Append one entry per critic review to the correlation JSONL.

    Returns the number of entries written. Safe no-op if there are no reviews.
    """
    reviews_list = list(reviews or [])
    if not reviews_list:
        return 0

    # Pick the latest (highest-round) review per agent_key — we want ONE row
    # per agent, not one per revise round.
    latest: dict[str, dict] = {}
    for r in reviews_list:
        key = r.get("agent_key", "")
        if not key:
            continue
        prev = latest.get(key)
        if prev is None or r.get("round", 0) >= prev.get("round", 0):
            latest[key] = r

    now = datetime.now().isoformat(timespec="seconds")
    path = _log_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)

    missing_info_by_agent = missing_info_by_agent or {}
    clarif_count_by_agent = clarif_count_by_agent or {}
    cost_ratio_by_agent   = cost_ratio_by_agent   or {}
    user_feedback         = user_feedback         or {}

    written = 0
    with _write_lock:
        with path.open("a", encoding="utf-8") as fh:
            for agent_key, r in latest.items():
                # Critic raw = score before any ScoreAdjuster penalty.
                # When score_original is populated, that's the raw value.
                critic_raw = r.get("score_original", r.get("score"))
                final      = r.get("score")
                entry = {
                    "session_id": session_id,
                    "timestamp":  now,
                    "agent_key":  agent_key,
                    "critic_raw": critic_raw,
                    "final":      final,
                    "signals": {
                        "test_pass_rate": test_pass_rate,
                        "missing_info":   missing_info_by_agent.get(agent_key, 0),
                        "clarif_count":   clarif_count_by_agent.get(agent_key, 0),
                        "cost_ratio":     cost_ratio_by_agent.get(agent_key),
                        "user_feedback":  user_feedback.get(agent_key),
                    },
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1
    return written


def load_entries(profile: str) -> list[dict]:
    """Return all logged entries as a list of dicts. Empty list nếu không file."""
    path = _log_path(profile)
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient. Returns None if undefined."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    denom_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denom_x == 0 or denom_y == 0:
        return None
    return num / (denom_x * denom_y)


def correlation_report(profile: str, agent_key: str | None = None) -> dict:
    """
    Compute Pearson correlation between `critic_raw` score and each outcome
    signal. Higher |r| → rubric tracks reality; |r| < 0.3 → rubric is weak.

    Returns dict:
        {
          "n": <sample count>,
          "by_agent": {"dev": {"test_pass_rate": 0.42, "missing_info": -0.31, ...}, ...}
        }
    """
    entries = load_entries(profile)
    if agent_key:
        entries = [e for e in entries if e.get("agent_key") == agent_key]

    if not entries:
        return {"n": 0, "by_agent": {}}

    # Group by agent_key
    by_agent: dict[str, list[dict]] = {}
    for e in entries:
        by_agent.setdefault(e.get("agent_key", "?"), []).append(e)

    out: dict[str, dict] = {}
    for ak, items in by_agent.items():
        if len(items) < 3:
            out[ak] = {"n": len(items), "note": "insufficient samples (<3)"}
            continue
        critic_raw = [float(x["critic_raw"]) for x in items
                       if x.get("critic_raw") is not None]
        sigs: dict[str, list[float]] = {}
        for name in ("test_pass_rate", "missing_info", "clarif_count",
                     "cost_ratio", "user_feedback"):
            ys: list[float] = []
            xs: list[float] = []
            for x in items:
                sig = x.get("signals", {}).get(name)
                if sig is None or x.get("critic_raw") is None:
                    continue
                try:
                    ys.append(float(sig))
                    xs.append(float(x["critic_raw"]))
                except (TypeError, ValueError):
                    continue
            if len(xs) >= 3:
                r = _pearson(xs, ys)
                if r is not None:
                    sigs[name] = round(r, 3)
        out[ak] = {"n": len(items), "pearson_vs_critic_raw": sigs}

    return {"n": len(entries), "by_agent": out}


def format_report(report: dict) -> str:
    """Pretty text summary for CLI output."""
    lines = [f"\n  📊 RUBRIC VALIDATION — {report['n']} total entries"]
    lines.append(f"  {'═'*60}")
    if report["n"] == 0:
        lines.append("  (no data yet — run a few sessions first)")
        return "\n".join(lines)

    lines.append("  Correlation of critic_raw vs outcome signals (Pearson r):")
    lines.append("  Interpretation: |r| ≥ 0.5 strong | 0.3-0.5 moderate | <0.3 weak")
    lines.append("")

    for ak, info in sorted(report["by_agent"].items()):
        n = info.get("n", 0)
        if "note" in info:
            lines.append(f"  [{ak.upper():8}] n={n:3}  — {info['note']}")
            continue
        lines.append(f"  [{ak.upper():8}] n={n:3}")
        for sig, r in info.get("pearson_vs_critic_raw", {}).items():
            if r is None:
                continue
            mark = "🟢" if abs(r) >= 0.5 else ("🟡" if abs(r) >= 0.3 else "🔴")
            # For negative-correlation signals (higher outcome = worse),
            # invert sign-display intuition:
            expected_sign = {
                "test_pass_rate": "+",   # higher pass rate → higher critic score expected
                "missing_info":   "-",   # more missing info → lower critic score expected
                "clarif_count":   "-",
                "cost_ratio":     "-",
                "user_feedback":  "+",
            }.get(sig, "")
            lines.append(f"    {mark} {sig:16} r={r:+.3f}  (expected {expected_sign})")

    lines.append(f"  {'═'*60}")
    return "\n".join(lines)
