"""
Score breakdown renderer — prints a per-agent "receipt" of how the final
score was arrived at (raw critic → every penalty/bonus → final).

Pure function, no orchestrator dependency — takes `critic_reviews` and
`adjustments` directly. Easy to unit-test.
"""
from __future__ import annotations

from typing import Iterable


_DISPLAY_ORDER = ("pm", "ba", "design", "techlead", "dev", "test")


def print_score_breakdown(
    critic_reviews: Iterable[dict],
    adjustments: Iterable[dict] | None = None,
    *,
    tprint=print,
) -> None:
    """
    Render a per-agent score receipt showing raw critic score, every
    penalty/bonus, and the final adjusted score.

    Example output::

        Dev score:  8  (critic raw)
          − 3.0    Patrol tests fail 30%
          − 2.0    2 MISSING_INFO leaked downstream
          ─────────────────────────────────────
          = 3      final

    Parameters
    ----------
    critic_reviews
        List of review dicts (with ``agent_key``, ``score``, optional
        ``score_original`` and ``score_adjustment``).
    adjustments
        Optional list of adjustment dicts (from `ScoreAdjuster.adjustments`).
        Unused in rendering but accepted for future extension.
    tprint
        Thread-safe print function. Default: built-in ``print``.
    """
    critic_reviews = list(critic_reviews or [])
    if not critic_reviews:
        return

    # Keep only the latest (highest-round) review per agent.
    latest: dict[str, dict] = {}
    for r in critic_reviews:
        key = r.get("agent_key", "?")
        prev = latest.get(key)
        if prev is None or r.get("round", 0) >= prev.get("round", 0):
            latest[key] = r

    tprint(f"\n  {'═'*60}")
    tprint(f"  📋 FINAL SCORE BREAKDOWN")
    tprint(f"  {'═'*60}")

    agents_present = [a for a in _DISPLAY_ORDER if a in latest] + \
                     [a for a in latest if a not in _DISPLAY_ORDER]

    for agent_key in agents_present:
        r = latest[agent_key]
        role = r.get("agent_role", agent_key.upper())
        raw = r.get("score_original", r.get("score", "?"))
        final = r.get("score", "?")
        adj_text = (r.get("score_adjustment") or "").strip()

        tprint(f"\n  {role} score:  {raw}  (critic raw)")

        if adj_text:
            for line in adj_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Strip the header produced by ScoreAdjustment.summary();
                # we already printed raw/final explicitly.
                if "→" in line and "/10" in line:
                    continue
                # Normalise leading markers so output aligns.
                if line.startswith(("−", "-", "+")):
                    tprint(f"    {line}")
                else:
                    tprint(f"    {line}")

        tprint(f"    {'─'*40}")
        tprint(f"    = {final}   final")

    tprint(f"\n  {'═'*60}")
