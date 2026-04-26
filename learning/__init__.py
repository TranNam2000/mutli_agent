"""
Meta-learning: skill/rule optimisers, REVISE history.

Historical note — two modules moved out of this package during the
architecture cleanup:
  * skill_selector + task_models → pipeline/ (routing + data types)
  * score_adjuster               → analyzer/ (pure transform)

Back-compat re-exports here so old `from learning import X` imports keep
working without code churn.
"""
# Back-compat re-exports from their new homes
from pipeline.skill_selector import (
    select_skill, detect_scope, list_skills, render_skill, llm_pick_skill, SCOPES,
)
from analyzer.score_adjuster import (       # noqa: F401
    ScoreAdjuster, ScoreAdjustment,
    count_clarifications_from_bus, count_missing_info,
)
# Genuinely in learning/
from .skill_optimizer import SkillOptimizer, SkillHistory
from .revise_history import ReviseHistory, AUTO_THRESHOLD

__all__ = [
    "select_skill", "detect_scope", "list_skills", "render_skill",
    "llm_pick_skill", "SCOPES",
    "SkillOptimizer", "SkillHistory",
    "ReviseHistory", "AUTO_THRESHOLD",
    "ScoreAdjuster", "ScoreAdjustment",
    "count_clarifications_from_bus", "count_missing_info",
]
