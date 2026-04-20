"""
IntegrityRules — persistent tables that RuleOptimizer mutates after it
studies `audit_log.jsonl`. Loaded by the orchestrator before every run so
"yesterday's mistakes" actively gate "today's skip decisions".

Layout under rules/<profile>/.learning/:

    module_blacklist.json    → { "payment": 3, "auth": 2, ... }
        value = failure count; any module at/above threshold forces Critic.

    keyword_risk.json        → { "deep link": "high", "animation": "med", ... }
        matched tokens auto-bump TaskMetadata.context.risk_level.

    agent_reputation.json    → { "BA": {"false_negatives": 1, "force_window_remaining": 4}, ... }
        When false_negatives ≥ threshold, the orchestrator forces Critic on
        that role for `force_window_remaining` upcoming tasks (decremented
        each time it fires).

All three files are plain JSON. Missing file = empty state (safe default).
"""
from __future__ import annotations
import json
import threading
from pathlib import Path


# Thresholds — conservative defaults; RuleOptimizer can tune them later.
MODULE_BLACKLIST_THRESHOLD = 3     # N failures on same module → forced Critic
REPUTATION_FN_THRESHOLD    = 2     # N false-negatives on same agent → force window
FORCED_CRITIC_WINDOW       = 5     # next N tasks get Critic regardless of metadata


class IntegrityRules:
    """Single-point-of-truth for skip-critic integrity gates."""

    def __init__(self, profile_dir: Path):
        self._dir = Path(profile_dir) / ".learning"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._bl_path  = self._dir / "module_blacklist.json"
        self._kw_path  = self._dir / "keyword_risk.json"
        self._rep_path = self._dir / "agent_reputation.json"
        self._lock = threading.Lock()

        self.module_blacklist: dict[str, int]      = self._load(self._bl_path) or {}
        self.keyword_risk:     dict[str, str]      = self._load(self._kw_path) or {}
        self.agent_reputation: dict[str, dict]     = self._load(self._rep_path) or {}

    # ── IO ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _load(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _flush(self) -> None:
        with self._lock:
            self._bl_path.write_text(
                json.dumps(self.module_blacklist, indent=2, ensure_ascii=False),
                encoding="utf-8")
            self._kw_path.write_text(
                json.dumps(self.keyword_risk, indent=2, ensure_ascii=False),
                encoding="utf-8")
            self._rep_path.write_text(
                json.dumps(self.agent_reputation, indent=2, ensure_ascii=False),
                encoding="utf-8")

    # ── Query API (read-side, used by orchestrator gate) ────────────────────

    def module_forces_critic(self, module: str) -> bool:
        """True if this module has earned a forced-Critic flag."""
        return (self.module_blacklist.get(module, 0) >= MODULE_BLACKLIST_THRESHOLD)

    def bump_risk_for_keywords(self, text: str, current_risk: str) -> str:
        """Raise `current_risk` up to the strongest matching keyword rule."""
        order = {"low": 0, "med": 1, "high": 2}
        cur = order.get(current_risk, 0)
        hay = (text or "").lower()
        for kw, level in self.keyword_risk.items():
            if kw.lower() in hay and order.get(level, 0) > cur:
                cur = order[level]
        inv = {v: k for k, v in order.items()}
        return inv[cur]

    def role_has_forced_window(self, role: str) -> bool:
        """True if this role is currently inside a forced-Critic window."""
        rep = self.agent_reputation.get(role, {})
        return int(rep.get("force_window_remaining", 0)) > 0

    def consume_forced_window(self, role: str) -> None:
        """Decrement the role's forced-Critic window by 1 after we honour it."""
        rep = self.agent_reputation.setdefault(role, {"false_negatives": 0,
                                                      "force_window_remaining": 0})
        if rep.get("force_window_remaining", 0) > 0:
            rep["force_window_remaining"] = rep["force_window_remaining"] - 1
            self._flush()

    # ── Mutation API (write-side, called by RuleOptimizer) ──────────────────

    def record_failure(self, *,
                       module: str | None,
                       impact_areas: list[str],
                       agent_in_charge: str,
                       skipped_roles: list[str],
                       blockers: list[str]) -> dict:
        """
        Update every table in a single transaction, given one audit record.

        Returns a dict describing what changed — useful for logging and for
        the orchestrator's post-run summary.
        """
        changed = {"modules_bumped": [], "roles_bumped": [],
                    "new_forced_windows": [], "keywords_promoted": []}

        # 1. Module blacklist — count for each impact area + top-level module.
        touched = set(impact_areas or [])
        if module:
            touched.add(module)
        for m in touched:
            before = self.module_blacklist.get(m, 0)
            after  = before + 1
            self.module_blacklist[m] = after
            changed["modules_bumped"].append({"module": m, "count": after})

        # 2. Agent reputation — penalise each role whose Critic was skipped.
        for role in skipped_roles:
            rep = self.agent_reputation.setdefault(role, {"false_negatives": 0,
                                                          "force_window_remaining": 0})
            rep["false_negatives"] = int(rep.get("false_negatives", 0)) + 1
            changed["roles_bumped"].append({"role": role,
                                             "false_negatives": rep["false_negatives"]})
            if rep["false_negatives"] >= REPUTATION_FN_THRESHOLD and \
               int(rep.get("force_window_remaining", 0)) == 0:
                rep["force_window_remaining"] = FORCED_CRITIC_WINDOW
                changed["new_forced_windows"].append(
                    {"role": role, "window": FORCED_CRITIC_WINDOW})

        # 3. Keyword risk — scan blockers for common failure nouns.
        import re
        text = " ".join(blockers).lower()
        candidates = {
            "deep link": "high", "deeplink": "high",
            "animation": "med",
            "payment": "high",
            "auth": "high",
            "widget tree": "med",
            "race condition": "high",
            "memory leak": "high",
            "null check": "med",
        }
        for kw, level in candidates.items():
            if re.search(rf"\b{re.escape(kw)}\b", text):
                prev = self.keyword_risk.get(kw)
                if prev != level:
                    self.keyword_risk[kw] = level
                    changed["keywords_promoted"].append({"keyword": kw, "risk": level})

        self._flush()
        return changed

    # ── Rule-file generator (the visible artefact from the user's demo) ──────

    def write_integrity_rules_md(self, profile_dir: Path) -> Path:
        """
        Emit a human-readable `integrity.md` summarising all three tables.
        This is the file RuleOptimizer shows off in the demo: "AI just wrote
        a new integrity rule — forever."
        """
        lines = ["# Integrity Rules (auto-generated by RuleOptimizer)",
                 "",
                 "_These rules override skip-Critic decisions to protect_",
                 "_components that have previously failed after being skipped._",
                 ""]

        if self.module_blacklist:
            lines.append("## Module Blacklist")
            lines.append(
                f"Any task whose `impact_area` or module matches the list "
                f"below will FORCE Critic (threshold ≥ {MODULE_BLACKLIST_THRESHOLD} "
                f"historical failures).\n")
            lines.append("| Module | Failures | Forces Critic? |")
            lines.append("|---|---|---|")
            for m, n in sorted(self.module_blacklist.items(), key=lambda x: -x[1]):
                mark = "✅" if n >= MODULE_BLACKLIST_THRESHOLD else ""
                lines.append(f"| `{m}` | {n} | {mark} |")
            lines.append("")

        if self.keyword_risk:
            lines.append("## Keyword Risk Boost")
            lines.append(
                "Tasks whose description contains any of the following "
                "keywords get `risk_level` auto-bumped to the given level:\n")
            lines.append("| Keyword | Risk |")
            lines.append("|---|---|")
            for k, r in sorted(self.keyword_risk.items(), key=lambda x: x[0]):
                lines.append(f"| `{k}` | **{r}** |")
            lines.append("")

        if self.agent_reputation:
            lines.append("## Agent Reputation")
            lines.append(
                f"When an agent accumulates ≥ {REPUTATION_FN_THRESHOLD} "
                f"false-negatives (skipped Critic, then failed QA), it is "
                f"placed in a forced-Critic window of "
                f"{FORCED_CRITIC_WINDOW} upcoming tasks.\n")
            lines.append("| Role | False-negatives | Force window remaining |")
            lines.append("|---|---|---|")
            for role, rep in sorted(self.agent_reputation.items(),
                                     key=lambda x: -int(x[1].get("false_negatives", 0))):
                fn = int(rep.get("false_negatives", 0))
                fw = int(rep.get("force_window_remaining", 0))
                lines.append(f"| {role} | {fn} | {fw} |")
            lines.append("")

        out = Path(profile_dir) / "integrity.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        return out
