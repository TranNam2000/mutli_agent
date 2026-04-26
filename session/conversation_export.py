"""
Conversation export — save full agent-to-agent message log as Markdown
+ structured JSON with session metadata, reviews, skills.

Extracted from orchestrator so output format changes don't require touching
pipeline flow.

Public API
----------
    save_conversations         (orch) -> None
    serialize_critic_reviews   (orch) -> list[dict]
    serialize_skills_used      (orch) -> dict
"""
from __future__ import annotations

import json
from datetime import datetime

from core.logging import tprint


def save_conversations(orch):
    """Save full agent-to-agent conversation log in both Markdown and JSON."""
    if not orch.bus.log:
        return

    # ── Markdown (human-readable, unchanged format) ──────────────────────
    lines = [f"# Agent Conversations – {orch.session_id}\n\n"]
    for i, msg in enumerate(orch.bus.log, 1):
        lines.append(f"## [{i}] {msg.from_agent} → {msg.to_agent}  _{msg.timestamp}_\n\n")
        lines.append(f"**Question:**\n{msg.content}\n\n")
        if msg.response:
            lines.append(f"**Answer:**\n{msg.response}\n\n")
        lines.append("---\n\n")
    md_path = orch.output_dir / f"{orch.session_id}_conversations.md"
    md_path.write_text("".join(lines), encoding="utf-8")
    tprint(f"  💬 Conversations saved → {md_path.name}")

    # ── JSON (structured, full metadata for analytics) ────────────────────
    payload = {
        "session_id":  orch.session_id,
        "profile":     orch.profile,
        "project": {
            "kind":  orch.project_info.kind if orch.project_info else None,
            "name":  orch.project_info.name if orch.project_info else None,
            "root":  str(orch.project_info.root) if orch.project_info else None,
        } if getattr(orch, "project_info", None) else None,
        "maintain_mode": orch.maintain_mode,
        "created_at":  datetime.now().isoformat(timespec="seconds"),
        "tokens":      orch.tokens.to_dict() if orch.tokens else None,
        "messages":    orch.bus.to_dict_list(),
        "reviews":     serialize_critic_reviews(orch),
        "skills":      serialize_skills_used(orch),
    }
    json_path = orch.output_dir / f"{orch.session_id}_conversations.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    tprint(f"  📦 Conversations JSON → {json_path.name}")


def serialize_critic_reviews(orch) -> list[dict]:
    """Export critic_reviews list, keeping only JSON-safe fields."""
    safe = []
    for r in getattr(orch, "critic_reviews", []):
        # Keep primitive fields only; drop any non-serializable extras
        entry = {}
        for k, v in r.items():
            if isinstance(v, (str, int, float, bool, type(None), list, dict)):
                entry[k] = v
            else:
                entry[k] = str(v)
        safe.append(entry)
    return safe


def serialize_skills_used(orch) -> dict:
    """Collect skill usage from every agent."""
    out: dict[str, list[dict]] = {}
    for key, agent in getattr(orch, "agents", {}).items():
        log = getattr(agent, "_skill_usage_log", [])
        if log:
            out[key] = list(log)
    return out
