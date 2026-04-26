"""Post-run analysis helpers — cost budgeting, score rendering, learning triggers.

Split out of orchestrator.py so each piece can be tested without spinning
up the full pipeline.
"""
from .cost_history import (
    COST_BUDGETS_DEFAULT,
    COST_FALLBACK,
    COST_RATIO_OVER_BUDGET,
    COST_TREND_WINDOW,
    COST_TREND_REQUIRED_OVER,
    load_cost_history,
    save_cost_history,
    load_cost_budgets,
    expected_budget_for_tasks,
)
from .score_renderer import print_score_breakdown
from .outcome_logger import (
    log_session_outcomes,
    load_entries as load_outcome_entries,
    correlation_report,
    format_report as format_correlation_report,
)
from .regression_classifier import (
    snapshot_features,
    train as train_regression_classifier,
    load_model as load_regression_model,
    should_apply as classifier_should_apply,
    format_status as format_classifier_status,
    backfill_labels,
    MIN_TRAINING_SAMPLES,
    FALLBACK_COUNT_THRESHOLD,
)
from .skill_outcome_logger import (
    log_session_skills,
    load_entries as load_skill_entries,
    skill_stats,
)
from .score_adjuster import (
    ScoreAdjuster,
    ScoreAdjustment,
    count_clarifications_from_bus,
    count_missing_info,
)
from .outcome_pipeline import analyze_session

__all__ = [
    "COST_BUDGETS_DEFAULT",
    "COST_FALLBACK",
    "COST_RATIO_OVER_BUDGET",
    "COST_TREND_WINDOW",
    "COST_TREND_REQUIRED_OVER",
    "load_cost_history",
    "save_cost_history",
    "load_cost_budgets",
    "expected_budget_for_tasks",
    "print_score_breakdown",
    "log_session_outcomes",
    "load_outcome_entries",
    "correlation_report",
    "format_correlation_report",
    "snapshot_features",
    "train_regression_classifier",
    "load_regression_model",
    "classifier_should_apply",
    "format_classifier_status",
    "backfill_labels",
    "MIN_TRAINING_SAMPLES",
    "FALLBACK_COUNT_THRESHOLD",
    "log_session_skills",
    "load_skill_entries",
    "skill_stats",
    "ScoreAdjuster",
    "ScoreAdjustment",
    "count_clarifications_from_bus",
    "count_missing_info",
    "analyze_session",
]
