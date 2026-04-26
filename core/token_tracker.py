"""Token usage tracker for multi-agent pipeline sessions."""
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from datetime import datetime


# Vietnamese text ≈ 2 chars/token, English ≈ 4 chars/token
# Mixed Vi/En content → 2.5 is a reasonable middle ground
_CHARS_PER_TOKEN = 2.5

# Thresholds
WARN_PCT  = 80   # print warning, keep going
PAUSE_PCT = 95   # pause and ask user


@dataclass
class CallRecord:
    agent:      str
    step:       str
    input_tok:  int
    output_tok: int
    timestamp:  str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

    @property
    def total(self) -> int:
        return self.input_tok + self.output_tok

    def to_dict(self) -> dict:
        return {
            "timestamp":  self.timestamp,
            "agent":      self.agent,
            "step":       self.step,
            "input_tok":  self.input_tok,
            "output_tok": self.output_tok,
            "total":      self.total,
        }


class TokenTracker:
    def __init__(self, budget: int = 500_000):
        """
        budget: estimated token quota for this session.
        Default 500k covers ~3-4 full pipeline runs on Sonnet.
        User can override via --budget flag.
        """
        self.budget   = budget
        self._used    = 0
        self.records: list[CallRecord] = []
        self._warned  = False
        self._lock    = threading.RLock()  # guards _used and records (reentrant)

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(self, agent_role: str, step: str, input_text: str, output_text: str) -> int:
        """Estimate and record token usage for one LLM call. Returns tokens used."""
        input_tok  = max(1, int(len(input_text)  / _CHARS_PER_TOKEN))
        output_tok = max(1, int(len(output_text) / _CHARS_PER_TOKEN))
        rec = CallRecord(agent_role, step, input_tok, output_tok)
        with self._lock:
            self.records.append(rec)
            self._used += rec.total
        return rec.total

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def pct(self) -> float:
        return (self.used / self.budget * 100) if self.budget else 0

    def should_warn(self) -> bool:
        with self._lock:
            return self.pct >= WARN_PCT and not self._warned

    def should_pause(self) -> bool:
        return self.pct >= PAUSE_PCT

    def mark_warned(self):
        with self._lock:
            self._warned = True

    # ── Reports ───────────────────────────────────────────────────────────────

    def short_status(self) -> str:
        bar_filled = int(self.pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        return f"[{bar}] {self.pct:.1f}%  {self.used:,} / {self.budget:,} tokens"

    def full_report(self) -> str:
        lines = [
            f"\n  {'═'*60}",
            f"  📊 TOKEN USAGE REPORT",
            f"  {'═'*60}",
            f"  Budget  : {self.budget:,} tokens",
            f"  Used    : {self.used:,} tokens ({self.pct:.1f}%)",
            f"  Remaining: {self.remaining:,} tokens",
            f"  {self.short_status()}",
            f"\n  Breakdown by agent:",
        ]

        # Group by agent
        by_agent: dict[str, int] = {}
        for r in self.records:
            by_agent[r.agent] = by_agent.get(r.agent, 0) + r.total
        for agent, total in sorted(by_agent.items(), key=lambda x: -x[1]):
            pct = total / self.used * 100 if self.used else 0
            lines.append(f"    {agent:<30} {total:>7,} tok  ({pct:.1f}%)")

        lines.append(f"\n  Total calls: {len(self.records)}")
        lines.append(f"  {'═'*60}")
        return "\n".join(lines)

    # ── JSON export ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize for JSON export."""
        by_agent: dict[str, int] = {}
        for r in self.records:
            by_agent[r.agent] = by_agent.get(r.agent, 0) + r.total
        return {
            "budget":     self.budget,
            "used":       self.used,
            "remaining":  self.remaining,
            "pct":        round(self.pct, 2),
            "call_count": len(self.records),
            "by_agent":   by_agent,
            "calls":      [r.to_dict() for r in self.records],
        }
