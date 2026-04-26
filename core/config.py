"""
Central env-var and config lookup.

Rule: never call `os.environ.get("MULTI_AGENT_*", ...)` scattered across
modules. Read through helpers here so:
  1. Defaults are documented in one place (cross-reference CLAUDE.md §6).
  2. Typos get caught (the helper knows every valid key).
  3. Tests can monkey-patch one module.
"""
from __future__ import annotations

import os
from typing import Literal


# ── Known env var defaults ───────────────────────────────────────────────────

_DEFAULTS: dict[str, str] = {
    # Learning loop
    "MULTI_AGENT_LEARNING_MODE":         "propose",   # propose | auto | off
    "MULTI_AGENT_LEARNING_DRY_RUN":      "0",
    "MULTI_AGENT_LEARNING_AUTO":         "0",         # legacy alias
    "MULTI_AGENT_SHADOW_AB_FORCE":       "0",
    # Skill selection — LLM picks by default; set HEURISTIC=1 to opt out
    "MULTI_AGENT_SKILL_LLM":             "1",   # legacy alias; effectively always on
    "MULTI_AGENT_SKILL_HEURISTIC":       "0",   # new opt-out switch
    "MULTI_AGENT_SKILL_MAX":             "2",
    # PM confirm-plan gate — set =1 in CI / non-interactive runs to skip
    # the "Proceed? [Y/n]" prompt
    "MULTI_AGENT_PM_AUTO_CONFIRM":       "0",
    # When no existing skill matches the task, auto-call SkillDesigner to
    # draft a fresh skill into skills/<agent>/auto/<slug>.md. Default ON.
    "MULTI_AGENT_AUTO_CREATE_SKILL":     "1",
    # Runtime
    "MULTI_AGENT_MAX_CONCURRENT":        "3",
    "MULTI_AGENT_CALL_SPACING_MS":       "100",
    "MULTI_AGENT_AUTO_COMMIT":           "1",
    "MULTI_AGENT_CRITIC_ALL":            "0",
    "MULTI_AGENT_DEBUG":                 "0",
    # Critic gating overrides
    "MULTI_AGENT_TL_CRITIC_ALWAYS":      "0",
    "MULTI_AGENT_TL_CRITIC_NEVER":       "0",
    "MULTI_AGENT_LEGACY_RULE_OPTIMIZER": "0",
    "MULTI_AGENT_RULE_CONFIRM":          "0",
    "MULTI_AGENT_NO_AUTO_FEEDBACK":      "0",
    "MULTI_AGENT_AUTO_HEAL":             "1",
    "MULTI_AGENT_SKILL_REVIEW":          "0",
}


def get(key: str) -> str:
    """Return raw env var value, falling back to registered default."""
    return os.environ.get(key, _DEFAULTS.get(key, ""))


def get_bool(key: str) -> bool:
    """`'1' / 'true' / 'yes'` → True (case-insensitive)."""
    val = get(key).strip().lower()
    return val in ("1", "true", "yes", "on")


def get_int(key: str, *, min_value: int | None = None,
            max_value: int | None = None) -> int:
    """Parse an int env var, clamped to [min, max] if provided."""
    val = get(key).strip()
    try:
        n = int(val)
    except (TypeError, ValueError):
        default = _DEFAULTS.get(key, "0")
        try:
            n = int(default)
        except ValueError:
            n = 0
    if min_value is not None and n < min_value:
        n = min_value
    if max_value is not None and n > max_value:
        n = max_value
    return n


def get_learning_mode() -> Literal["propose", "auto", "off"]:
    """Canonical learning mode — honours legacy MULTI_AGENT_LEARNING_AUTO."""
    if get_bool("MULTI_AGENT_LEARNING_AUTO"):
        return "auto"
    mode = get("MULTI_AGENT_LEARNING_MODE").strip().lower()
    if mode not in ("propose", "auto", "off"):
        return "propose"
    return mode  # type: ignore[return-value]
