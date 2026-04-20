"""Meta-learning: skill selection, skill optimizer, rule history, score adjuster."""
from .skill_selector import (
    select_skill, detect_scope, list_skills, render_skill, llm_pick_skill, SCOPES,
)
from .skill_optimizer import SkillOptimizer, SkillHistory
from .revise_history import ReviseHistory, AUTO_THRESHOLD

# score_adjuster is optional — only import if file exists with expected symbols
try:
    from .score_adjuster import *  # noqa: F401, F403
except ImportError:
    pass

__all__ = [
    "select_skill", "detect_scope", "list_skills", "render_skill",
    "llm_pick_skill", "SCOPES",
    "SkillOptimizer", "SkillHistory",
    "ReviseHistory", "AUTO_THRESHOLD",
]
