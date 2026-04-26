"""
Skill-level optimizer — meta-learning at the skill granularity.

Complements rule_optimizer (which tunes rules/criteria text) by:
  1. Tracking per-skill success rate (avg critic score when that skill was active)
  2. Detecting chronic REVISE that can't fit any existing skill → suggest NEW skill
  3. Deprecating skills that consistently underperform
  4. Auto-tuning skill triggers when misfires detected

Storage: skills/.skill_history.json (per-profile).
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime
from pathlib import Path

from core.paths import SKILLS_DIR as _SKILLS_DIR
from core.logging import tprint

NEW_SKILL_THRESHOLD = 4       # ≥N sessions of misfit pattern → propose new skill
DEPRECATE_THRESHOLD_AVG = 5.0 # avg score below this across ≥5 uses → deprecate
DEPRECATE_MIN_USES = 5

# REFINE band — skills in this score range get tuned (not deprecated, not ignored)
REFINE_MIN_AVG = 5.0
REFINE_MAX_AVG = 7.0
REFINE_MIN_USES = 4

# Shadow mode — new/refined skill trial period
SHADOW_MIN_USES = 2           # new skill stays in shadow for this many runs
SHADOW_PROMOTE_MARGIN = 0.5   # must beat the skill it replaces by this score margin

# Merge — two skills are candidate to merge if trigger overlap exceeds this
MERGE_TRIGGER_OVERLAP = 0.7


class SkillHistory:
    def __init__(self, path: Path | None = None):
        self.path = path or (_SKILLS_DIR / ".skill_history.json")
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Record skill usage + outcome ──────────────────────────────────────────

    def record_usage(self, agent_key: str, skill_key: str, scope: str,
                     score: float, session_id: str, verdict: str):
        """Log one use of a skill with the critic's verdict."""
        key = f"{agent_key}:{skill_key}"
        if key not in self._data:
            self._data[key] = {
                "agent_key":  agent_key,
                "skill_key":  skill_key,
                "uses":       0,
                "pass_count": 0,
                "total_score": 0.0,
                "scores":     [],  # recent history for shadow compare
                "scopes":     {},
                "last_used":  "",
                "deprecated": False,
                "status":     "stable",  # stable | shadow | refined
                "parent_skill": None,    # if this is a shadow replacement for another skill
            }
        entry = self._data[key]
        entry["uses"] += 1
        entry["total_score"] += score
        entry["scores"].append({"score": score, "session_id": session_id,
                                 "at": datetime.now().isoformat()})
        entry["scores"] = entry["scores"][-20:]  # cap
        entry["scopes"][scope] = entry["scopes"].get(scope, 0) + 1
        if verdict == "PASS":
            entry["pass_count"] += 1
        entry["last_used"] = datetime.now().isoformat()
        entry["last_session"] = session_id
        self._save()

    def record_misfit(self, agent_key: str, task_snippet: str, reason: str, session_id: str):
        """Record when no skill fit the task well — candidate for a new skill."""
        key = f"__misfit__{agent_key}"
        if key not in self._data:
            self._data[key] = {"agent_key": agent_key, "patterns": []}

        # Dedupe by fingerprint
        fp = re.sub(r"[^a-z0-9\s]", "", reason.lower())[:80]
        existing = [p for p in self._data[key]["patterns"] if p["fp"] == fp]
        if existing:
            existing[0]["count"] += 1
            existing[0]["last_seen"] = datetime.now().isoformat()
        else:
            self._data[key]["patterns"].append({
                "fp":         fp,
                "reason":     reason[:200],
                "task_sample": task_snippet[:150],
                "count":      1,
                "first_seen": datetime.now().isoformat(),
                "last_seen":  datetime.now().isoformat(),
                "sessions":   [session_id],
            })
        self._save()

    # ── Queries ───────────────────────────────────────────────────────────────

    def avg_score(self, agent_key: str, skill_key: str) -> float:
        entry = self._data.get(f"{agent_key}:{skill_key}", {})
        if not entry.get("uses"):
            return 0.0
        return entry["total_score"] / entry["uses"]

    def pass_rate(self, agent_key: str, skill_key: str) -> float:
        entry = self._data.get(f"{agent_key}:{skill_key}", {})
        if not entry.get("uses"):
            return 0.0
        return entry["pass_count"] / entry["uses"]

    def underperforming_skills(self) -> list[dict]:
        """Skills with too many uses but low avg score."""
        result = []
        for key, entry in self._data.items():
            if ":" not in key or key.startswith("__"):
                continue
            if entry.get("deprecated"):
                continue
            if (entry.get("uses", 0) >= DEPRECATE_MIN_USES
                    and self.avg_score(entry["agent_key"], entry["skill_key"]) < DEPRECATE_THRESHOLD_AVG):
                result.append(entry)
        return result

    def refinable_skills(self) -> list[dict]:
        """Skills in the mediocre band — candidates for REFINE."""
        result = []
        for key, entry in self._data.items():
            if ":" not in key or key.startswith("__"):
                continue
            if entry.get("deprecated") or entry.get("status") == "shadow":
                continue
            avg = self.avg_score(entry["agent_key"], entry["skill_key"])
            if (entry.get("uses", 0) >= REFINE_MIN_USES
                    and REFINE_MIN_AVG <= avg <= REFINE_MAX_AVG):
                # Check if score is stagnant (not improving)
                scores = [s["score"] for s in entry.get("scores", [])[-4:]]
                if len(scores) < 4 or max(scores) - min(scores) < 1.5:
                    result.append({**entry, "avg_score": avg})
        return result

    def shadows_ready_to_judge(self) -> list[dict]:
        """Shadow skills that have enough uses to compare against parent."""
        result = []
        for key, entry in self._data.items():
            if ":" not in key or key.startswith("__"):
                continue
            if entry.get("status") != "shadow":
                continue
            if entry.get("uses", 0) < SHADOW_MIN_USES:
                continue
            parent = entry.get("parent_skill")
            if not parent:
                continue
            parent_key = f"{entry['agent_key']}:{parent}"
            parent_entry = self._data.get(parent_key)
            if not parent_entry:
                continue
            shadow_avg = self.avg_score(entry["agent_key"], entry["skill_key"])
            parent_avg = self.avg_score(entry["agent_key"], parent)
            result.append({
                "shadow_entry": entry,
                "parent_entry": parent_entry,
                "shadow_avg":   shadow_avg,
                "parent_avg":   parent_avg,
                "delta":        shadow_avg - parent_avg,
            })
        return result

    def mark_status(self, agent_key: str, skill_key: str, status: str, parent: str | None = None):
        key = f"{agent_key}:{skill_key}"
        if key not in self._data:
            return
        self._data[key]["status"] = status
        if parent is not None:
            self._data[key]["parent_skill"] = parent
        self._save()

    def misfit_patterns(self, agent_key: str) -> list[dict]:
        entry = self._data.get(f"__misfit__{agent_key}", {})
        return [p for p in entry.get("patterns", []) if p["count"] >= NEW_SKILL_THRESHOLD]

    def mark_deprecated(self, agent_key: str, skill_key: str):
        key = f"{agent_key}:{skill_key}"
        if key in self._data:
            self._data[key]["deprecated"] = True
            self._data[key]["deprecated_at"] = datetime.now().isoformat()
            self._save()


class SkillOptimizer:
    """Orchestrator-facing façade — analyses history and applies changes."""

    def __init__(self, profile: str = "default"):
        self.profile = profile
        self.history = SkillHistory()

    def record_from_critic_reviews(self, reviews: list[dict], skill_usage: list[dict],
                                    session_id: str):
        """Called after each pipeline run to update the skill history."""
        # Build a map: step → skill that was active
        step_to_skill = {
            u["step"]: (u["skill"], u.get("scope", "?"))
            for u in skill_usage if u.get("step")
        }
        for r in reviews:
            step = r.get("agent_key", "")
            skill_info = step_to_skill.get(step)
            if not skill_info:
                continue
            skill_key, scope = skill_info
            self.history.record_usage(
                agent_key=step,
                skill_key=skill_key,
                scope=scope,
                score=float(r.get("score", 0)),
                session_id=session_id,
                verdict=r.get("verdict", ""),
            )
            # If REVISE & weaknesses look like the skill didn't match task → record misfit
            if r.get("verdict") == "REVISE":
                reason = "; ".join(r.get("weaknesses", [])[:2])[:180]
                if reason:
                    self.history.record_misfit(step, r.get("raw", "")[:200], reason, session_id)

    def suggest_new_skills(self) -> list[dict]:
        """Propose new skill files when a chronic misfit pattern is detected AND
        no existing skill covers the pattern well (trigger overlap check)."""
        suggestions = []
        for agent_dir in _SKILLS_DIR.iterdir():
            if not agent_dir.is_dir() or agent_dir.name.startswith("."):
                continue
            misfits = self.history.misfit_patterns(agent_dir.name)
            existing_triggers = self._existing_triggers(agent_dir.name)

            for pattern in misfits:
                # Dedupe: skip if existing skill already has >40% trigger overlap
                overlap = self._trigger_overlap(pattern["reason"], existing_triggers)
                if overlap > 0.4:
                    continue
                suggestions.append({
                    "agent_key":   agent_dir.name,
                    "pattern":     pattern["reason"],
                    "task_sample": pattern["task_sample"],
                    "count":       pattern["count"],
                    "proposed_skill_key": self._propose_skill_key(
                        pattern["reason"], pattern["task_sample"], agent_dir.name),
                    "action":      "CREATE",
                })
        return suggestions

    def suggest_refinements(self) -> list[dict]:
        """Skills stuck in the 5-7 band → propose REFINE."""
        suggestions = []
        for entry in self.history.refinable_skills():
            scores = [s["score"] for s in entry.get("scores", [])[-6:]]
            suggestions.append({
                "agent_key":  entry["agent_key"],
                "skill_key":  entry["skill_key"],
                "avg_score":  entry["avg_score"],
                "recent":     scores,
                "uses":       entry.get("uses", 0),
                "action":     "REFINE",
            })
        return suggestions

    def suggest_merges(self) -> list[dict]:
        """Detect near-duplicate skills (>70% trigger overlap, same agent, same scope)."""
        suggestions = []
        for agent_dir in _SKILLS_DIR.iterdir():
            if not agent_dir.is_dir() or agent_dir.name.startswith("."):
                continue
            skills = self._load_agent_skills(agent_dir.name)
            for i, a in enumerate(skills):
                for b in skills[i+1:]:
                    if set(a["scope"]) != set(b["scope"]):
                        continue
                    overlap = self._set_overlap(a["triggers"], b["triggers"])
                    if overlap >= MERGE_TRIGGER_OVERLAP:
                        suggestions.append({
                            "agent_key": agent_dir.name,
                            "skill_a":   a["skill_key"],
                            "skill_b":   b["skill_key"],
                            "overlap":   overlap,
                            "action":    "MERGE",
                        })
        return suggestions

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _existing_triggers(self, agent_key: str) -> list[str]:
        """Collect all triggers from existing skills of this agent."""
        triggers: list[str] = []
        for skill in self._load_agent_skills(agent_key):
            triggers.extend(skill["triggers"])
        return triggers

    def _load_agent_skills(self, agent_key: str) -> list[dict]:
        try:
            from pipeline.skill_selector import list_skills
            return list_skills(agent_key)
        except (ImportError, ValueError, KeyError, AttributeError):
            return []

    @staticmethod
    def _set_overlap(a: list[str], b: list[str]) -> float:
        if not a or not b:
            return 0.0
        sa, sb = set(a), set(b)
        return len(sa & sb) / min(len(sa), len(sb))

    def _trigger_overlap(self, reason: str, existing_triggers: list[str]) -> float:
        reason_words = set(re.findall(r"\b\w{4,}\b", reason.lower()))
        if not reason_words or not existing_triggers:
            return 0.0
        trigger_words: set[str] = set()
        for t in existing_triggers:
            trigger_words |= set(re.findall(r"\b\w{4,}\b", t.lower()))
        if not trigger_words:
            return 0.0
        return len(reason_words & trigger_words) / len(reason_words)

    def _propose_skill_key(self, reason: str, task_sample: str = "", agent_key: str = "") -> str:
        """Generate snake_case skill key — prefer task context over raw reason."""
        # Pull nouns / verbs from both reason + task
        text = f"{task_sample} {reason}".lower()
        # Strip common fluff words
        STOPWORDS = {"thiếu", "missing", "agent", "no", "not yet", "none",
                     "lack", "lacks", "no enough", "there", "this", "that", "with"}
        words = [w for w in re.findall(r"\b[a-zA-Z]{4,}\b", text)
                 if w not in STOPWORDS][:4]
        key = "_".join(words) or f"{agent_key}_custom"

        # Dedupe against existing skill keys
        existing = {s["skill_key"] for s in self._load_agent_skills(agent_key)}
        base_key = key
        i = 2
        while key in existing:
            key = f"{base_key}_v{i}"
            i += 1
        return key

    # ── Skill file ops ────────────────────────────────────────────────────────

    def write_new_skill(self, agent_key: str, skill_key: str, skill_content: str,
                        *, shadow_for: str | None = None) -> Path:
        """Create a new skill file. If shadow_for is set, mark as shadow replacement."""
        target = _SKILLS_DIR / agent_key / f"{skill_key}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill_content, encoding="utf-8")
        # Register history entry preemptively so shadow status is known
        status = "shadow" if shadow_for else "stable"
        self.history.mark_status(agent_key, skill_key, status, parent=shadow_for)
        return target

    def refine_skill(self, agent_key: str, skill_key: str, refined_content: str) -> tuple[Path, str]:
        """Replace skill content with refined version. Backs up original.
        Returns (new_path, backup_path_str). Original is kept as shadow parent."""
        src = _SKILLS_DIR / agent_key / f"{skill_key}.md"
        if not src.exists():
            raise FileNotFoundError(f"Skill not found: {src}")

        # Backup original
        backup_dir = _SKILLS_DIR / ".backups"
        backup_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"{agent_key}__{skill_key}__{ts}.md"
        backup.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        # Create a shadow version instead of overwriting directly
        shadow_key = f"{skill_key}_v2"
        shadow_key = self._unique_key(agent_key, shadow_key)
        self.write_new_skill(agent_key, shadow_key, refined_content, shadow_for=skill_key)
        return _SKILLS_DIR / agent_key / f"{shadow_key}.md", str(backup)

    def _unique_key(self, agent_key: str, proposed: str) -> str:
        existing = {s["skill_key"] for s in self._load_agent_skills(agent_key)}
        if proposed not in existing:
            return proposed
        i = 2
        while f"{proposed}_v{i}" in existing:
            i += 1
        return f"{proposed}_v{i}"

    def promote_shadow(self, shadow_entry: dict) -> str:
        """Shadow passed A/B → replace parent with shadow content."""
        agent_key = shadow_entry["agent_key"]
        shadow_key = shadow_entry["skill_key"]
        parent_key = shadow_entry["parent_skill"]
        if not parent_key:
            return "no_parent"

        shadow_path = _SKILLS_DIR / agent_key / f"{shadow_key}.md"
        parent_path = _SKILLS_DIR / agent_key / f"{parent_key}.md"
        if not shadow_path.exists() or not parent_path.exists():
            return "missing_files"

        # Atomic write: write to .tmp then replace to avoid corrupt state on crash
        tmp_path = parent_path.with_suffix(".tmp")
        tmp_path.write_text(shadow_path.read_text(encoding="utf-8"), encoding="utf-8")
        os.replace(tmp_path, parent_path)
        # Retire shadow file
        shadow_path.rename(shadow_path.with_suffix(".retired.md"))
        self.history.mark_status(agent_key, shadow_key, "retired_promoted")
        return "promoted"

    def demote_shadow(self, shadow_entry: dict) -> str:
        """Shadow failed A/B → delete shadow file, keep parent."""
        agent_key  = shadow_entry["agent_key"]
        shadow_key = shadow_entry["skill_key"]
        shadow_path = _SKILLS_DIR / agent_key / f"{shadow_key}.md"
        if shadow_path.exists():
            shadow_path.rename(shadow_path.with_suffix(".rejected.md"))
        self.history.mark_status(agent_key, shadow_key, "retired_rejected")
        return "rejected"

    def deprecate_underperforming(self) -> list[tuple[str, str, float]]:
        """Mark skills with avg score below threshold as deprecated.
        BUT only if agent has ≥1 other stable skill (don't leave agent skillless)."""
        changes = []
        for entry in self.history.underperforming_skills():
            agent_key = entry["agent_key"]
            skill_key = entry["skill_key"]

            # Safety: count stable non-deprecated skills for this agent
            active_skills = [
                s for s in self._load_agent_skills(agent_key)
                if s["skill_key"] != skill_key
            ]
            if len(active_skills) == 0:
                continue  # can't deprecate the only skill

            avg = self.history.avg_score(agent_key, skill_key)
            skill_path = _SKILLS_DIR / agent_key / f"{skill_key}.md"
            if skill_path.exists():
                # Rename to double extension so skill_selector (which globs *.md) ignores it
                skill_path.rename(skill_path.with_name(f"{skill_key}.md.deprecated"))
            self.history.mark_deprecated(agent_key, skill_key)
            changes.append((agent_key, skill_key, avg))
        return changes

    # ── Display ───────────────────────────────────────────────────────────────

    def print_stats(self):
        tprint(f"\n  {'═'*60}")
        tprint(f"  🎯 SKILL PERFORMANCE (profile: {self.profile})")
        tprint(f"  {'═'*60}")
        rows = []
        for key, entry in self.history._data.items():
            if ":" not in key or key.startswith("__"):
                continue
            if entry.get("deprecated"):
                continue
            avg = self.history.avg_score(entry["agent_key"], entry["skill_key"])
            pr  = self.history.pass_rate(entry["agent_key"], entry["skill_key"])
            rows.append((entry["agent_key"], entry["skill_key"],
                         entry.get("uses", 0), avg, pr))
        rows.sort(key=lambda r: (-r[2], r[3]))
        for agent, skill, uses, avg, pr in rows[:12]:
            icon = "✅" if avg >= 7 else ("⚠️ " if avg >= 5 else "❌")
            tprint(f"  {icon} {agent:10} / {skill:30} uses={uses:3}  avg={avg:.1f}  pass={pr*100:.0f}%")
        tprint(f"  {'─'*60}")
