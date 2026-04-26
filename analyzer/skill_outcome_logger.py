"""
Skill outcome logger — pair every skill activation with the final agent score
for that session. Feeds skill_optimizer's promote/demote decisions with data
that actually reflects downstream quality, not just critic-round metrics.

Storage: rules/<profile>/.learning/skill_outcomes.jsonl

Line format::

    {
      "session_id": "...",
      "timestamp":  "...",
      "agent_key":  "ba",
      "skill_key":  "full_product",
      "rank":       1,
      "method":     "keyword" | "llm",
      "scope":      "feature",
      "final_score": 7,        # agent final score this session
      "test_pass_rate": 0.9,   # if applicable
      "signals": { ...same as outcome_logger... }
    }
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from core.paths import learning_dir


_write_lock = threading.Lock()


def _path(profile: str) -> Path:
    return learning_dir(profile) / "skill_outcomes.jsonl"


def log_session_skills(
    profile: str,
    session_id: str,
    agent_skill_logs: dict[str, list[dict]],
    reviews: Iterable[dict],
    *,
    test_pass_rate: float | None = None,
    missing_info_by_agent: dict[str, int] | None = None,
    clarif_count_by_agent: dict[str, int] | None = None,
    cost_ratio_by_agent: dict[str, float] | None = None,
) -> int:
    """
    Cross-join (agent, active skills) × final score for that agent.
    One line per (agent_key, skill_key).
    """
    if not agent_skill_logs:
        return 0

    # Pick latest review per agent_key
    latest: dict[str, dict] = {}
    for r in reviews or []:
        k = r.get("agent_key", "")
        if not k:
            continue
        prev = latest.get(k)
        if prev is None or r.get("round", 0) >= prev.get("round", 0):
            latest[k] = r

    now = datetime.now().isoformat(timespec="seconds")
    path = _path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with _write_lock:
        with path.open("a", encoding="utf-8") as fh:
            for agent_key, skill_entries in agent_skill_logs.items():
                if not skill_entries:
                    continue
                review = latest.get(agent_key)
                if not review:
                    continue
                final_score = review.get("score")
                for entry in skill_entries:
                    row = {
                        "session_id":    session_id,
                        "timestamp":     now,
                        "agent_key":     agent_key,
                        "skill_key":     entry.get("skill"),
                        "rank":          entry.get("rank", 1),
                        "method":        entry.get("method"),
                        "scope":         entry.get("scope"),
                        "step":          entry.get("step"),
                        "final_score":   final_score,
                        "critic_raw":    review.get("score_original", final_score),
                        "signals": {
                            "test_pass_rate": test_pass_rate,
                            "missing_info":   (missing_info_by_agent or {}).get(agent_key, 0),
                            "clarif_count":   (clarif_count_by_agent or {}).get(agent_key, 0),
                            "cost_ratio":     (cost_ratio_by_agent   or {}).get(agent_key),
                        },
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    written += 1
    return written


def load_entries(profile: str) -> list[dict]:
    path = _path(profile)
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


def skill_stats(profile: str) -> dict:
    """
    Aggregate: per (agent_key, skill_key) → {n, avg_score, avg_critic_raw,
    p_test_pass, avg_missing_info}. Used by skill_optimizer and by the
    `mag rubric-classifier` command for situational awareness.
    """
    entries = load_entries(profile)
    buckets: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        ak, sk = e.get("agent_key", ""), e.get("skill_key", "")
        if not ak or not sk:
            continue
        buckets.setdefault((ak, sk), []).append(e)

    out: dict[str, dict] = {}
    for (ak, sk), rows in buckets.items():
        scores = [float(r["final_score"]) for r in rows
                   if r.get("final_score") is not None]
        crits  = [float(r["critic_raw"]) for r in rows
                   if r.get("critic_raw") is not None]
        tp = [float(r["signals"]["test_pass_rate"]) for r in rows
               if r.get("signals", {}).get("test_pass_rate") is not None]
        mi = [int(r["signals"].get("missing_info", 0)) for r in rows]
        entry = {
            "n":            len(rows),
            "avg_score":    sum(scores) / len(scores) if scores else None,
            "avg_critic":   sum(crits)  / len(crits)  if crits  else None,
            "test_pass":    sum(tp) / len(tp) if tp else None,
            "missing_info": sum(mi) / len(mi) if mi else 0.0,
        }
        out[f"{ak}:{sk}"] = entry
    return out
