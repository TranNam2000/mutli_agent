"""
Back-compat re-exports — learning logic split into focused modules.
New code should import directly from the target module:

    from learning.rule_runner  import run_rule_optimizer, run_rule_evolver, build_cost_suggestions, ...
    from learning.skill_runner import run_skill_optimizer, apply_refine, apply_create, apply_merge
    from learning.trends       import print_score_trends
"""
from __future__ import annotations

# Rule side
from .rule_runner import (       # noqa: F401
    rule_path_for,
    run_rule_optimizer,
    run_rule_evolver,
    build_cost_suggestions,
)
from .rule_lifecycle import maybe_upgrade_criteria  # noqa: F401
# Skill side
from .skill_runner import (      # noqa: F401
    run_skill_optimizer,
    judge_shadow_skills,
    apply_refine,
    apply_create,
    apply_merge,
)
# Trends
from .trends import print_score_trends  # noqa: F401


__all__ = [
    "rule_path_for",
    "run_rule_optimizer",
    "run_rule_evolver",
    "build_cost_suggestions",
    "maybe_upgrade_criteria",
    "run_skill_optimizer",
    "judge_shadow_skills",
    "apply_refine", "apply_create", "apply_merge",
    "print_score_trends",
]
