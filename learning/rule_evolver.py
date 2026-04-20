"""
RuleEvolver — production-grade rule evolution layer.

Combines four input signals into a single decision stream:

    1) Critic REVISE patterns       (RuleOptimizer LLM analysis)
    2) IntegrityRules state          (module blacklist, keyword risk, reputation)
    3) User feedback                 (mag feedback CLI — see feedback_store)
    4) Cost signals                  (ScoreAdjuster per-agent token pressure)

Each suggestion carries PROVENANCE (which source produced it, session id,
timestamp) and a MULTI-DIM SCORE (correctness / cost / usability /
consistency). The evolver routes each suggestion to one of three lanes:

    AUTO-APPLY   — ≥ 3 consensus sources agree AND multi_dim ≥ 0.8
    SHADOW A/B   — multi_dim ∈ [0.6, 0.8) — write .shadow.md, compare in
                   upcoming sessions, promote or demote
    PENDING      — multi_dim < 0.6 — surface to user via Concierge log

Files touched
-------------
    rules/<profile>/<agent>.md
        annotated with inline HTML comments tagging provenance
    rules/<profile>/<agent>.shadow.md
        shadow version created when a suggestion is in A/B mode
    rules/<profile>/.feedback/<session_id>.jsonl
        user feedback written by mag feedback CLI
    rules/<profile>/.shadow_log.json
        shadow run history (odd/even alternation, scores per variant)
"""
from __future__ import annotations
import json
import os
import shutil
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path


# Multi-dim score thresholds.
AUTO_APPLY_THRESHOLD   = 0.80
SHADOW_THRESHOLD       = 0.60
AUTO_APPLY_CONSENSUS   = 3         # number of distinct sources that must agree
SHADOW_MIN_SESSIONS    = 2         # min sessions before shadow can promote
SHADOW_PROMOTE_DELTA   = 0.30      # shadow must beat baseline by this much


# Provenance source identifiers.
SRC_LLM        = "llm"
SRC_INTEGRITY  = "integrity"
SRC_USER       = "user"
SRC_COST       = "cost"


@dataclass
class Suggestion:
    """A single rule-file ADD proposal with attribution + scoring."""
    agent_key:       str                       # "ba", "dev", ...
    target_type:     str                       # "rule" | "criteria"
    addition:        str                       # markdown snippet to append
    reason:          str                       # 1-sentence rationale
    sources:         list[str] = field(default_factory=list)  # provenance chain
    session_id:      str = ""
    # Multi-dim scoring (0..1 each; final = weighted avg)
    score_correctness: float = 0.0             # does it reduce real errors?
    score_cost:        float = 0.0             # does it avoid token bloat?
    score_usability:   float = 0.0             # does it reduce clarifications?
    score_consistency: float = 0.0             # does it not contradict existing rules?
    # Populated post-merge.
    multi_dim:       float = 0.0
    lane:            str   = ""                # "auto" | "shadow" | "pending"
    timestamp:       str   = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def provenance_tag(self) -> str:
        """Inline HTML comment tagging source for audit."""
        src = "+".join(self.sources) or "unknown"
        return (f"<!-- provenance: src={src} session={self.session_id or 'n/a'} "
                f"ts={self.timestamp} score={self.multi_dim:.2f} -->")

    def dedup_key(self) -> str:
        """Used to merge duplicate suggestions across sources."""
        return f"{self.agent_key}|{self.target_type}|{self.addition.strip()[:80]}"


# ── Scoring ──────────────────────────────────────────────────────────────────

def _score_consistency(addition: str, current_rule: str) -> float:
    """Heuristic: penalise if addition contradicts something obvious."""
    if not current_rule:
        return 1.0
    add_lower = addition.lower()
    cur_lower = current_rule.lower()
    # Obvious contradiction: "must" + "must not" on same subject
    import re as _re
    must_tokens = _re.findall(r"must\s+not\s+\w+|must\s+\w+", add_lower)
    for tok in must_tokens:
        negated = tok.replace("must not ", "must ") if "not" in tok else tok.replace("must ", "must not ")
        if negated in cur_lower:
            return 0.2     # direct contradiction
    # Duplication: addition appears nearly verbatim in current rule
    if addition.strip()[:60] in current_rule:
        return 0.4
    return 1.0


def _score_from_sources(sources: list[str]) -> float:
    """Source-weighted boost — user feedback is highest trust."""
    weights = {
        SRC_USER:      1.0,
        SRC_INTEGRITY: 0.85,
        SRC_COST:      0.7,
        SRC_LLM:       0.6,
    }
    return max(weights.get(s, 0.5) for s in sources) if sources else 0.0


def compute_multi_dim(s: Suggestion, current_rule: str = "") -> float:
    """Populate multi-dim fields and return final weighted score."""
    # Correctness defaults to source-weighted proxy if not set.
    if s.score_correctness == 0.0:
        s.score_correctness = _score_from_sources(s.sources)
    if s.score_consistency == 0.0:
        s.score_consistency = _score_consistency(s.addition, current_rule)
    if s.score_usability == 0.0:
        s.score_usability = 0.8   # neutral default
    if s.score_cost == 0.0:
        # Short additions cost less — penalize verbose additions.
        s.score_cost = max(0.3, 1.0 - len(s.addition) / 2000)

    # Consensus: how many sources proposed similar thing?
    consensus_boost = 1.0 + min(0.2, 0.05 * (len(s.sources) - 1))
    raw = (
        s.score_correctness * 0.40 +
        s.score_consistency * 0.30 +
        s.score_usability   * 0.15 +
        s.score_cost        * 0.15
    )
    s.multi_dim = min(1.0, raw * consensus_boost)
    return s.multi_dim


def assign_lane(s: Suggestion) -> str:
    """Route suggestion to auto / shadow / pending."""
    if s.multi_dim >= AUTO_APPLY_THRESHOLD and len(s.sources) >= AUTO_APPLY_CONSENSUS:
        s.lane = "auto"
    elif s.multi_dim >= SHADOW_THRESHOLD:
        s.lane = "shadow"
    else:
        s.lane = "pending"
    return s.lane


# ── Merge duplicate suggestions ──────────────────────────────────────────────

def merge_suggestions(batches: list[list[Suggestion]]) -> list[Suggestion]:
    """Merge across sources; same addition across sources → union of sources."""
    by_key: dict[str, Suggestion] = {}
    for batch in batches:
        for s in batch:
            key = s.dedup_key()
            if key in by_key:
                existing = by_key[key]
                for src in s.sources:
                    if src not in existing.sources:
                        existing.sources.append(src)
                # Take max of each dim score so far
                existing.score_correctness = max(existing.score_correctness, s.score_correctness)
                existing.score_cost        = max(existing.score_cost,        s.score_cost)
                existing.score_usability   = max(existing.score_usability,   s.score_usability)
                existing.score_consistency = max(existing.score_consistency, s.score_consistency)
            else:
                by_key[key] = s
    return list(by_key.values())


# ── Rule file writing with provenance ────────────────────────────────────────

def write_provenance_addition(rule_path: Path, suggestion: Suggestion) -> Path | None:
    """Append the addition to rule file with an inline provenance comment."""
    rule_path = Path(rule_path)
    rule_path.parent.mkdir(parents=True, exist_ok=True)
    existing = rule_path.read_text(encoding="utf-8") if rule_path.exists() else ""
    # Backup before write.
    if existing:
        backup_dir = rule_path.parent.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"{rule_path.name}.{ts}.bak"
        backup.write_text(existing, encoding="utf-8")
    block = "\n\n" + suggestion.provenance_tag() + "\n" + suggestion.addition.rstrip() + "\n"
    rule_path.write_text(existing + block, encoding="utf-8")
    return rule_path


def write_shadow_rule(rule_path: Path, suggestion: Suggestion) -> Path:
    """Create <agent>.shadow.md next to the baseline. Appended with provenance."""
    shadow_path = rule_path.with_suffix(".shadow.md")
    base = rule_path.read_text(encoding="utf-8") if rule_path.exists() else ""
    shadow_body = base + "\n\n" + suggestion.provenance_tag() + "\n" + suggestion.addition.rstrip() + "\n"
    shadow_path.write_text(shadow_body, encoding="utf-8")
    return shadow_path


# ── Shadow A/B log ───────────────────────────────────────────────────────────

class ShadowLog:
    """Track baseline vs shadow scores over sessions for rule A/B."""

    def __init__(self, profile_dir: Path):
        self._path = Path(profile_dir) / ".shadow_log.json"
        self._lock = threading.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            return {"variants": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"variants": {}}

    def _flush(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def register(self, agent_key: str, target_type: str,
                  shadow_path: str, suggestion_key: str):
        """Record that a shadow exists and start tracking scores."""
        key = f"{agent_key}:{target_type}"
        self._data["variants"].setdefault(key, {
            "agent_key":    agent_key,
            "target_type":  target_type,
            "shadow_path":  shadow_path,
            "suggestion":   suggestion_key,
            "baseline":     [],
            "shadow":       [],
            "created_at":   datetime.now().isoformat(timespec="seconds"),
        })
        self._flush()

    def log_run(self, agent_key: str, target_type: str,
                 variant: str, score: float, session_id: str):
        """Record a score for this variant ('baseline' | 'shadow')."""
        key = f"{agent_key}:{target_type}"
        if key not in self._data["variants"]:
            return
        self._data["variants"][key][variant].append({
            "session_id": session_id, "score": score,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })
        self._flush()

    def verdicts(self) -> list[dict]:
        """Return list of shadow entries ready for promote/demote decision."""
        out = []
        for key, v in self._data["variants"].items():
            b = v.get("baseline", [])
            s = v.get("shadow", [])
            if len(b) < SHADOW_MIN_SESSIONS or len(s) < SHADOW_MIN_SESSIONS:
                continue
            avg_b = sum(x["score"] for x in b) / len(b)
            avg_s = sum(x["score"] for x in s) / len(s)
            delta = avg_s - avg_b
            if delta >= SHADOW_PROMOTE_DELTA:
                verdict = "promote"
            elif delta <= -SHADOW_PROMOTE_DELTA:
                verdict = "demote"
            else:
                verdict = "continue"
            out.append({
                "key": key, "verdict": verdict,
                "baseline_avg": avg_b, "shadow_avg": avg_s, "delta": delta,
                "variant": v,
            })
        return out

    def drop(self, key: str):
        """Remove a variant after promote/demote."""
        self._data["variants"].pop(key, None)
        self._flush()


# ── User feedback store ──────────────────────────────────────────────────────

class FeedbackStore:
    """Read/write user feedback from mag feedback CLI."""

    def __init__(self, profile_dir: Path):
        self._dir = Path(profile_dir) / ".feedback"
        self._dir.mkdir(parents=True, exist_ok=True)

    def record(self, session_id: str, agent_key: str,
                rating: int, comment: str = "") -> Path:
        """Append one feedback entry for a session."""
        entry = {
            "session_id": session_id,
            "agent_key":  agent_key,
            "rating":     max(1, min(5, int(rating))),
            "comment":    comment.strip(),
            "ts":         datetime.now().isoformat(timespec="seconds"),
        }
        path = self._dir / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    def load_all(self, since_days: int = 30) -> list[dict]:
        """Load every feedback entry within the window."""
        cutoff = datetime.now().timestamp() - since_days * 86400
        out: list[dict] = []
        for fp in self._dir.glob("*.jsonl"):
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    ts = datetime.fromisoformat(e.get("ts", "")).timestamp()
                    if ts >= cutoff:
                        out.append(e)
                except Exception:
                    continue
        return out

    def suggestions_from_feedback(self, entries: list[dict]) -> list[Suggestion]:
        """Translate low-rating feedback into rule-tightening suggestions."""
        sugs: list[Suggestion] = []
        # Aggregate per agent: mean rating, bad comments.
        from collections import defaultdict
        buckets: dict[str, list[dict]] = defaultdict(list)
        for e in entries:
            buckets[e["agent_key"]].append(e)
        for agent, items in buckets.items():
            mean = sum(i["rating"] for i in items) / len(items)
            if mean >= 3.5:
                continue  # agent doing fine
            low = [i for i in items if i["rating"] <= 2 and i.get("comment")]
            comment_digest = "; ".join(i["comment"][:60] for i in low[:3]) or "low rating average"
            addition = (
                f"- User feedback signal: mean rating = {mean:.1f}/5 across "
                f"{len(items)} session(s). Recent low-rating issues: "
                f"{comment_digest}. Address these explicitly in output."
            )
            sugs.append(Suggestion(
                agent_key=agent, target_type="rule",
                addition=addition,
                reason=f"User feedback mean {mean:.1f}/5 — tighten behavior",
                sources=[SRC_USER],
                session_id=items[-1].get("session_id", ""),
                score_correctness=1.0,   # user is authoritative
                score_usability=1.0,     # by definition: fixing usability
            ))
        return sugs


# ── Main evolver entry point ─────────────────────────────────────────────────

class RuleEvolver:
    """Orchestrates all four signal sources into a single update stream."""

    def __init__(self, profile_dir: Path, session_id: str = ""):
        self.profile_dir = Path(profile_dir)
        self.session_id  = session_id
        self.feedback    = FeedbackStore(self.profile_dir)
        self.shadow_log  = ShadowLog(self.profile_dir)

    def gather(self, *,
                llm_suggestions:       list[dict] | None = None,
                integrity_suggestions: list[dict] | None = None,
                cost_suggestions:      list[Suggestion] | None = None,
                ) -> list[Suggestion]:
        """Convert heterogeneous inputs into Suggestion objects + merge."""
        batches: list[list[Suggestion]] = []

        for src, raw in (
            (SRC_LLM,       llm_suggestions or []),
            (SRC_INTEGRITY, integrity_suggestions or []),
        ):
            conv: list[Suggestion] = []
            for d in raw:
                conv.append(Suggestion(
                    agent_key   = d["agent_key"],
                    target_type = d.get("target_type", "rule"),
                    addition    = d["addition"],
                    reason      = d.get("reason", ""),
                    sources     = [src],
                    session_id  = self.session_id,
                ))
            batches.append(conv)

        user_fb = self.feedback.load_all()
        batches.append(self.feedback.suggestions_from_feedback(user_fb))

        if cost_suggestions:
            batches.append(cost_suggestions)

        return merge_suggestions(batches)

    def decide(self, suggestions: list[Suggestion],
                current_rules: dict[str, str]) -> list[Suggestion]:
        """Score every suggestion + assign a lane. `current_rules` map
        {f"{agent_key}:{target_type}": existing content} for consistency check."""
        for s in suggestions:
            key = f"{s.agent_key}:{s.target_type}"
            compute_multi_dim(s, current_rules.get(key, ""))
            assign_lane(s)
        # Sort: auto first (apply), then shadow (A/B), then pending.
        priority = {"auto": 0, "shadow": 1, "pending": 2}
        suggestions.sort(key=lambda s: (priority[s.lane], -s.multi_dim))
        return suggestions

    def apply(self, suggestions: list[Suggestion],
               rule_path_resolver) -> dict:
        """Execute: write auto to baseline, write shadow to <agent>.shadow.md.
        `rule_path_resolver(agent_key, target_type) -> Path` tells us where to
        write. Returns {"applied": [...], "shadowed": [...], "pending": [...]}.
        """
        out = {"applied": [], "shadowed": [], "pending": []}
        for s in suggestions:
            path = rule_path_resolver(s.agent_key, s.target_type)
            if s.lane == "auto":
                write_provenance_addition(path, s)
                out["applied"].append(asdict(s))
            elif s.lane == "shadow":
                shadow_path = write_shadow_rule(path, s)
                self.shadow_log.register(
                    agent_key      = s.agent_key,
                    target_type    = s.target_type,
                    shadow_path    = str(shadow_path),
                    suggestion_key = s.dedup_key(),
                )
                out["shadowed"].append(asdict(s))
            else:
                out["pending"].append(asdict(s))
        return out

    def evaluate_shadows(self, rule_path_resolver) -> list[dict]:
        """Check live shadow runs; promote winners, demote losers.
        Returns action log."""
        actions: list[dict] = []
        for v in self.shadow_log.verdicts():
            key = v["key"]
            info = v["variant"]
            agent_key, target_type = key.split(":", 1)
            base_path = rule_path_resolver(agent_key, target_type)
            shadow_path = Path(info["shadow_path"])
            if v["verdict"] == "promote" and shadow_path.exists():
                # Promote: shadow becomes baseline (rename + keep baseline as .rejected.md)
                rejected = base_path.with_suffix(".rejected.md")
                if base_path.exists():
                    shutil.copyfile(base_path, rejected)
                shutil.copyfile(shadow_path, base_path)
                shadow_path.unlink(missing_ok=True)
                actions.append({"action": "promote", "key": key, "delta": v["delta"]})
                self.shadow_log.drop(key)
            elif v["verdict"] == "demote":
                shadow_path.unlink(missing_ok=True)
                actions.append({"action": "demote", "key": key, "delta": v["delta"]})
                self.shadow_log.drop(key)
            else:
                actions.append({"action": "continue", "key": key, "delta": v["delta"]})
        return actions


# ── Provenance reader (for audit / CLI display) ──────────────────────────────

def parse_provenance_from_rule(rule_path: Path) -> list[dict]:
    """Scan a rule file for `<!-- provenance: ... -->` markers."""
    import re as _re
    if not rule_path.exists():
        return []
    text = rule_path.read_text(encoding="utf-8")
    pattern = _re.compile(
        r"<!--\s*provenance:\s*src=([^\s]+)\s+session=([^\s]+)\s+ts=([^\s]+)\s+score=([\d.]+)\s*-->",
        _re.IGNORECASE,
    )
    return [
        {"src": m.group(1), "session": m.group(2),
         "ts":  m.group(3), "score":   float(m.group(4))}
        for m in pattern.finditer(text)
    ]
