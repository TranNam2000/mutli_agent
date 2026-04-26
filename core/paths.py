"""
Central path registry — single source of truth for every directory this
project needs at runtime.

Rule: never hand-roll `Path(__file__).parent.parent / "rules"` etc. in other
modules. Import the constants (or helpers) from here. If a path layout
changes, there's exactly one place to update.
"""
from __future__ import annotations

from pathlib import Path


# ── Repository layout ────────────────────────────────────────────────────────

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
RULES_DIR:   Path = PROJECT_ROOT / "rules"
SKILLS_DIR:  Path = PROJECT_ROOT / "skills"
OUTPUTS_DIR: Path = PROJECT_ROOT / "outputs"


# ── Per-profile helpers ──────────────────────────────────────────────────────

def profile_dir(profile: str) -> Path:
    """rules/<profile>/"""
    return RULES_DIR / profile


def learning_dir(profile: str) -> Path:
    """rules/<profile>/.learning/ — where JSON/JSONL learning state lives."""
    return profile_dir(profile) / ".learning"


def criteria_dir(profile: str) -> Path:
    """rules/<profile>/criteria/ — rubric files consumed by CriticAgent."""
    return profile_dir(profile) / "criteria"


def backups_dir() -> Path:
    """rules/backups/ — snapshot of rule files before each apply."""
    return RULES_DIR / "backups"


# ── Per-agent skill helpers ──────────────────────────────────────────────────

def agent_skills_dir(agent_key: str) -> Path:
    """skills/<agent_key>/ — all skill markdown files for an agent."""
    return SKILLS_DIR / agent_key


# ── Ensure directories exist (called at startup by orchestrator) ─────────────

def ensure_profile_tree(profile: str) -> None:
    """Create profile directories if they don't exist. Safe to call repeatedly."""
    for d in (profile_dir(profile), learning_dir(profile), criteria_dir(profile),
              backups_dir()):
        d.mkdir(parents=True, exist_ok=True)


def rule_path_for(profile: str, agent_key: str, target_type: str) -> Path:
    """Resolve rules/<profile>/<agent>.md vs rules/<profile>/criteria/<agent>.md.

    target_type ∈ {"rule", "criteria"}.
    """
    base = profile_dir(profile)
    if target_type == "criteria":
        return base / "criteria" / f"{agent_key}.md"
    return base / f"{agent_key}.md"
