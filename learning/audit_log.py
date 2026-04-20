"""
Audit log — the "black box" for Fail-fast / Learn-faster.

When a task that had Critic skipped later fails at Dev build or QA run,
we record a single structured JSON line per failure. Cross-session
accumulation powers RuleOptimizer's module blacklist and keyword
risk-boost.

File layout:
    <session_dir>/audit_log.jsonl           # per-session, append-only
    <rules>/<profile>/.audit/aggregate.jsonl # profile-level rollup

Record shape:

    {
      "timestamp":          "2026-04-20T06:31:12",
      "session_id":         "20260420_063112_abcd",
      "task_id":            "BUG-12345",
      "predicted_metadata": {...},         # what we classified it as
      "skipped_for_roles":  ["PM","BA","TechLead"],
      "actual_outcome":     "qa_crash",    # qa_crash|build_error|logic_error|blocker
      "blockers":           ["TC-001 BLOCKER: missing widget key payButton", ...],
      "agent_in_charge":    "Dev",
      "root_cause_hint":    "BA spec missing widget tree; skip_critic at BA"
    }
"""
from __future__ import annotations
import json
import threading
from datetime import datetime
from pathlib import Path


# Canonical failure categories.
OUTCOME_QA_CRASH     = "qa_crash"
OUTCOME_BUILD_ERROR  = "build_error"
OUTCOME_LOGIC_ERROR  = "logic_error"
OUTCOME_BLOCKER      = "blocker"


class AuditLog:
    """
    Append-only JSONL sink for false-negative failures.

    One instance per session; writes are thread-safe.
    """

    def __init__(self, session_dir: Path, profile_dir: Path | None = None):
        self._session_path = Path(session_dir) / "audit_log.jsonl"
        self._session_path.parent.mkdir(parents=True, exist_ok=True)
        self._aggregate_path: Path | None = None
        if profile_dir is not None:
            agg = Path(profile_dir) / ".audit" / "aggregate.jsonl"
            agg.parent.mkdir(parents=True, exist_ok=True)
            self._aggregate_path = agg
        self._lock = threading.Lock()

    # ── Writing ──────────────────────────────────────────────────────────────

    def record(self, *,
               session_id: str,
               task_id: str,
               predicted_metadata: dict,
               skipped_for_roles: list[str],
               actual_outcome: str,
               blockers: list[str],
               agent_in_charge: str,
               root_cause_hint: str = "") -> dict:
        entry = {
            "timestamp":          datetime.now().isoformat(timespec="seconds"),
            "session_id":         session_id,
            "task_id":            task_id,
            "predicted_metadata": predicted_metadata,
            "skipped_for_roles":  skipped_for_roles,
            "actual_outcome":     actual_outcome,
            "blockers":           blockers[:20],   # cap for readability
            "agent_in_charge":    agent_in_charge,
            "root_cause_hint":    root_cause_hint,
        }
        raw = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self._session_path.open("a", encoding="utf-8") as f:
                f.write(raw + "\n")
            if self._aggregate_path is not None:
                with self._aggregate_path.open("a", encoding="utf-8") as f:
                    f.write(raw + "\n")
        return entry

    # ── Reading / aggregation ────────────────────────────────────────────────

    @classmethod
    def load_aggregate(cls, profile_dir: Path) -> list[dict]:
        """Load all historical false-negative entries from the profile rollup."""
        path = Path(profile_dir) / ".audit" / "aggregate.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    @classmethod
    def module_failure_counts(cls, profile_dir: Path) -> dict[str, int]:
        """Count per-module failures for RuleOptimizer to blacklist."""
        counts: dict[str, int] = {}
        for entry in cls.load_aggregate(profile_dir):
            impact = (entry.get("predicted_metadata", {})
                           .get("technical_debt", {})
                           .get("impact_area", []))
            for area in impact:
                counts[area] = counts.get(area, 0) + 1
        return counts


# ── Helpers ──────────────────────────────────────────────────────────────────

def classify_outcome(blockers: list[str]) -> str:
    """Map BLOCKER strings → coarse outcome category."""
    text = " ".join(blockers[:5]).lower()
    if any(k in text for k in ("crash", "exception", "null", "segfault")):
        return OUTCOME_QA_CRASH
    if any(k in text for k in ("build fail", "compile error", "compilation",
                                "does not compile", "unresolved reference")):
        return OUTCOME_BUILD_ERROR
    if any(k in text for k in ("wrong output", "incorrect result", "expected",
                                "mismatch", "does not match")):
        return OUTCOME_LOGIC_ERROR
    return OUTCOME_BLOCKER


def make_root_cause_hint(meta: dict, skipped_roles: list[str],
                         blockers: list[str]) -> str:
    """Build a short human-readable root-cause sentence for the log."""
    impact = meta.get("technical_debt", {}).get("impact_area", [])
    risk   = meta.get("context", {}).get("risk_level", "unknown")
    modules = ", ".join(impact) or "unspecified"
    roles = "+".join(skipped_roles) or "none"
    first_blocker = (blockers[0] if blockers else "")[:60]
    return (f"Task tagged risk={risk} impact=[{modules}] but failed with "
            f"'{first_blocker}'. Critic was skipped for {roles}.")
