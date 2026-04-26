"""
Score-trend renderer — per-agent sparkline + protected PASS patterns.
"""
from __future__ import annotations

from core.logging import tprint


_SPARK = " ▁▂▃▄▅▆▇█"


def print_score_trends(orch, history) -> None:
    """Per-agent score trends with sparklines across sessions."""
    agent_keys = [r["agent_key"] for r in orch.critic_reviews]
    seen: set[str] = set()
    rows = []
    for key in agent_keys:
        if key in seen:
            continue
        seen.add(key)
        entry = history._data.get(f"__trend__{key}", {})
        scores_raw = [s["score"] for s in entry.get("scores", [])]
        if len(scores_raw) < 2:
            continue
        recent = scores_raw[-10:]
        rows.append((key, _sparkline(recent), history.score_trend(key)))

    # Protected PASS patterns
    protected: list[str] = []
    for key in seen:
        patterns = history.get_pass_patterns(key)
        if patterns:
            top = patterns[0]["sample"][:60]
            protected.append(f"[{key}] {top}")
    if protected:
        tprint(f"\n  🛡️  PASS patterns được bảo vệ ({len(protected)}):")
        for p in protected[:4]:
            tprint(f"     • {p}")

    if not rows:
        return

    tprint(f"\n  📈 Score trends (last ≤10 sessions per agent):")
    for key, spark, trend in rows:
        tprint(f"     {key:10} {spark}  {trend}")


def _sparkline(scores: list[float]) -> str:
    if not scores:
        return ""
    lo, hi = min(scores), max(scores)
    span = hi - lo or 1
    return "".join(_SPARK[min(8, int((s - lo) / span * 8))] for s in scores)


