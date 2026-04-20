"""Persistent REVISE pattern tracker — auto-applies rule improvements when confidence threshold met."""
from __future__ import annotations
import json
import re
from datetime import datetime
from pathlib import Path

AUTO_THRESHOLD = 5  # auto-apply after this many occurrences
REGRESSION_THRESHOLD = 0.5   # score drop > this → regression
REGRESSION_MIN_SESSIONS = 2  # need at least this many post-apply sessions to judge

# Synonym groups — words in the same group are treated as identical
_SYNONYMS: list[set[str]] = [
    {"edge case", "edge cases", "edgecase", "boundary", "boundary value"},
    {"missing", "lacks", "lack", "absent", "no ", "without"},
    {"acceptance criteria", "ac", "acceptance criterion"},
    {"error handling", "error handle", "exception handling"},
    {"test case", "test cases", "testcase"},
    {"unit test", "unit tests", "unittest"},
    {"performance", "perf"},
    {"security", "auth", "authentication", "authorization"},
]


def _fingerprint(text: str) -> str:
    """Stable key: normalize synonyms, lowercase alphanumeric, first 120 chars."""
    normalized = text.lower()
    for group in _SYNONYMS:
        canonical = sorted(group)[0]
        for word in group:
            normalized = normalized.replace(word, canonical)
    cleaned = re.sub(r"[^a-z0-9\s]", "", normalized)
    return " ".join(cleaned.split())[:120]


class ReviseHistory:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _key(self, agent_key: str, reason: str, target_type: str) -> str:
        return f"{agent_key}:{target_type}:{_fingerprint(reason)}"

    # ── PASS pattern recording ────────────────────────────────────────────────

    def record_pass(self, agent_key: str, strengths: list[str], session_id: str):
        """Record what made an agent PASS — used to protect good patterns from being removed."""
        pass_key = f"__pass__{agent_key}"
        if pass_key not in self._data:
            self._data[pass_key] = {"agent_key": agent_key, "patterns": []}
        for strength in strengths:
            fp = _fingerprint(strength)
            existing = [p for p in self._data[pass_key]["patterns"] if p["fp"] == fp]
            if existing:
                existing[0]["count"] += 1
                existing[0]["last_seen"] = datetime.now().isoformat()
            else:
                self._data[pass_key]["patterns"].append({
                    "fp": fp,
                    "sample": strength[:120],
                    "count": 1,
                    "first_seen": datetime.now().isoformat(),
                    "last_seen": datetime.now().isoformat(),
                    "session_id": session_id,
                })
        # Keep top 20 patterns by count
        self._data[pass_key]["patterns"].sort(key=lambda p: p["count"], reverse=True)
        self._data[pass_key]["patterns"] = self._data[pass_key]["patterns"][:20]
        self._save()

    def conflicts_with_pass_patterns(self, agent_key: str, addition: str) -> str | None:
        """
        Check if a proposed rule addition would conflict with known good (PASS) patterns.
        Returns the conflicting pattern sample if found, None otherwise.
        """
        pass_key = f"__pass__{agent_key}"
        patterns = self._data.get(pass_key, {}).get("patterns", [])
        # Only consider patterns seen >= 2 times (consistent, not one-off)
        sin_patterns = [p for p in patterns if p["count"] >= 2]
        if not sin_patterns:
            return None

        addition_words = set(re.findall(r"\b\w{4,}\b", addition.lower()))
        for p in sin_patterns:
            pattern_words = set(re.findall(r"\b\w{4,}\b", p["sample"].lower()))
            overlap = addition_words & pattern_words
            # If addition shares >50% words with a PASS pattern → likely conflict
            if pattern_words and len(overlap) / len(pattern_words) > 0.50:
                return p["sample"]
        return None

    def get_pass_patterns(self, agent_key: str) -> list[dict]:
        """Return sin PASS patterns for an agent (count >= 2)."""
        patterns = self._data.get(f"__pass__{agent_key}", {}).get("patterns", [])
        return [p for p in patterns if p["count"] >= 2]

    # ── Criteria upgrade ──────────────────────────────────────────────────────

    UPGRADE_AVG_THRESHOLD = 8.5   # avg score to trigger upgrade
    UPGRADE_MIN_SESSIONS  = 3     # number session liên tiếp need đạt

    def should_upgrade_criteria(self, agent_key: str) -> bool:
        """
        True if agent liên tục PASS cao in UPGRADE_MIN_SESSIONS session gần nhất.
        Chỉ trigger max 1 time mỗi 5 session to tránh upgrade quá nhanh.
        """
        scores = self._data.get(f"__trend__{agent_key}", {}).get("scores", [])
        if len(scores) < self.UPGRADE_MIN_SESSIONS:
            return False

        recent = scores[-self.UPGRADE_MIN_SESSIONS:]
        avg = sum(s["score"] for s in recent) / len(recent)
        if avg < self.UPGRADE_AVG_THRESHOLD:
            return False

        # Cooldown: no upgrade if  upgrade in 5 session gần nhất
        upgrade_key = f"__upgrade__{agent_key}"
        last_upgrade_idx = self._data.get(upgrade_key, {}).get("last_score_idx", -999)
        current_idx = len(scores) - 1
        if current_idx - last_upgrade_idx < 5:
            return False

        return True

    def mark_upgraded(self, agent_key: str):
        upgrade_key = f"__upgrade__{agent_key}"
        scores = self._data.get(f"__trend__{agent_key}", {}).get("scores", [])
        entry = self._data.get(upgrade_key, {"count": 0})
        entry["last_score_idx"] = len(scores) - 1
        entry["upgraded_at"]    = datetime.now().isoformat()
        entry["count"]          = entry.get("count", 0) + 1
        self._data[upgrade_key] = entry
        self._save()

    def get_current_threshold(self, agent_key: str, base_threshold: int) -> int:
        """Return current threshold including all upgrades applied."""
        upgrade_key = f"__upgrade__{agent_key}"
        upgrade_count = self._data.get(upgrade_key, {}).get("count", 0)
        return min(base_threshold + upgrade_count, 10)

    # ── REVISE pattern recording ──────────────────────────────────────────────

    def record(self, agent_key: str, reason: str, addition: str, target_type: str) -> int:
        """Record a REVISE weakness pattern. Returns updated count. Skips blacklisted."""
        key = self._key(agent_key, reason, target_type)
        if self._data.get(key, {}).get("failed", False):
            return 0  # blacklisted — don't count again

        now = datetime.now().isoformat()
        if key not in self._data:
            self._data[key] = {
                "agent_key": agent_key,
                "target_type": target_type,
                "fingerprint": _fingerprint(reason),
                "reason_sample": reason[:120],
                "addition_sample": addition[:200],
                "count": 0,
                "first_seen": now,
                "last_seen": now,
                "applied": False,
                "failed": False,
            }
        entry = self._data[key]
        entry["count"] += 1
        entry["last_seen"] = now
        entry["addition_sample"] = addition[:200]
        self._save()
        return entry["count"]

    def should_auto_apply(self, agent_key: str, reason: str, target_type: str) -> bool:
        entry = self._data.get(self._key(agent_key, reason, target_type), {})
        return (
            entry.get("count", 0) >= AUTO_THRESHOLD
            and not entry.get("applied", False)
            and not entry.get("failed", False)
        )

    def is_blacklisted(self, agent_key: str, reason: str, target_type: str) -> bool:
        return self._data.get(self._key(agent_key, reason, target_type), {}).get("failed", False)

    def mark_applied(self, agent_key: str, reason: str, target_type: str,
                     backup_path: str = "", apply_session_id: str = ""):
        key = self._key(agent_key, reason, target_type)
        if key in self._data:
            self._data[key]["applied"] = True
            self._data[key]["applied_at"] = datetime.now().isoformat()
            self._data[key]["backup_path"] = backup_path
            self._data[key]["apply_session_id"] = apply_session_id
            self._save()

    def mark_failed(self, agent_key: str, reason: str, target_type: str):
        """Blacklist this pattern — will never be suggested or counted again."""
        key = self._key(agent_key, reason, target_type)
        if key in self._data:
            self._data[key]["failed"] = True
            self._data[key]["failed_at"] = datetime.now().isoformat()
            self._save()

    def get_count(self, agent_key: str, reason: str, target_type: str) -> int:
        return self._data.get(self._key(agent_key, reason, target_type), {}).get("count", 0)

    # ── Applied entries for regression check ─────────────────────────────────

    def get_applied_entries(self) -> list[dict]:
        """Return all entries that were applied but not yet failed/rolled back."""
        return [
            {**entry, "_key": key}
            for key, entry in self._data.items()
            if isinstance(entry, dict)
            and not key.startswith("__")
            and entry.get("applied", False)
            and not entry.get("failed", False)
            and not entry.get("rolled_back", False)
        ]

    # ── Score tracking ────────────────────────────────────────────────────────

    def record_score(self, agent_key: str, score: float, session_id: str):
        trend_key = f"__trend__{agent_key}"
        if trend_key not in self._data:
            self._data[trend_key] = {"agent_key": agent_key, "scores": []}
        self._data[trend_key]["scores"].append({
            "score": score,
            "session_id": session_id,
            "at": datetime.now().isoformat(),
        })
        self._data[trend_key]["scores"] = self._data[trend_key]["scores"][-20:]
        self._save()

    def score_trend(self, agent_key: str) -> str:
        entry = self._data.get(f"__trend__{agent_key}", {})
        scores = entry.get("scores", [])
        if len(scores) < 2:
            return ""
        vals = [s["score"] for s in scores[-5:]]
        arrow = "↑" if vals[-1] > vals[0] else ("↓" if vals[-1] < vals[0] else "→")
        return " → ".join(f"{v:.1f}" for v in vals) + f" {arrow}"

    def detect_regression(self, agent_key: str, apply_session_id: str) -> bool:
        """
        True if score consistently dropped after apply_session_id.
        Needs >= REGRESSION_MIN_SESSIONS post-apply data points.
        """
        scores = self._data.get(f"__trend__{agent_key}", {}).get("scores", [])
        pivot = next((i for i, s in enumerate(scores) if s["session_id"] == apply_session_id), None)
        if pivot is None or pivot < 2:
            return False
        after_scores = scores[pivot + 1:]  # sessions AFTER apply
        if len(after_scores) < REGRESSION_MIN_SESSIONS:
            return False  # not enough data yet
        before_avg = sum(s["score"] for s in scores[max(0, pivot - 2):pivot]) / min(2, pivot)
        after_avg  = sum(s["score"] for s in after_scores) / len(after_scores)
        return after_avg < before_avg - REGRESSION_THRESHOLD

    def mark_rolled_back(self, key: str):
        if key in self._data:
            self._data[key]["rolled_back"] = True
            self._data[key]["rolled_back_at"] = datetime.now().isoformat()
            self._save()

    # ── Checklist item tracking ───────────────────────────────────────────────

    EASY_ITEM_MIN_SESSIONS = 5  # number session liên tiếp YES 100% new coi is "quá dễ"

    def record_checklist_answers(self, agent_key: str, items: list[str],
                                  answers: dict[int, bool], session_id: str):
        """Track per-item YES/NO rate to detect items that are always passing."""
        ck_key = f"__checklist__{agent_key}"
        if ck_key not in self._data:
            self._data[ck_key] = {"agent_key": agent_key, "items": {}}

        for idx, item in enumerate(items, start=1):
            fp = _fingerprint(item)
            entry = self._data[ck_key]["items"].setdefault(fp, {
                "sample": item[:120],
                "yes_count": 0,
                "total_count": 0,
                "last_seen": "",
            })
            entry["total_count"] += 1
            if answers.get(idx, False):
                entry["yes_count"] += 1
            entry["last_seen"] = datetime.now().isoformat()

        self._save()

    def get_easy_items(self, agent_key: str, min_sessions: int | None = None) -> list[dict]:
        """Return items with 100% YES rate across >= min_sessions sessions."""
        threshold = min_sessions or self.EASY_ITEM_MIN_SESSIONS
        ck_key = f"__checklist__{agent_key}"
        items = self._data.get(ck_key, {}).get("items", {})
        easy = [
            {"sample": e["sample"], "total_count": e["total_count"], "agent_key": agent_key}
            for e in items.values()
            if e.get("total_count", 0) >= threshold and e.get("yes_count") == e.get("total_count")
        ]
        easy.sort(key=lambda x: x["total_count"], reverse=True)
        return easy

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def cleanup(self, days: int = 30) -> int:
        """Remove applied+non-failed entries older than `days` days."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        to_delete = []
        for key, entry in self._data.items():
            if not isinstance(entry, dict) or key.startswith("__"):
                continue
            if not entry.get("applied", False):
                continue
            applied_at = entry.get("applied_at", "")
            if not applied_at:
                continue
            try:
                dt = datetime.fromisoformat(applied_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).days >= days:
                    to_delete.append(key)
            except ValueError:
                pass
        for key in to_delete:
            del self._data[key]
        if to_delete:
            self._save()
        return len(to_delete)
