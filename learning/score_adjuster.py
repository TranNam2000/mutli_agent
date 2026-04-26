"""Back-compat shim — score_adjuster moved to analyzer/ (pure transform,
not a learning component). Import from `analyzer.score_adjuster` instead."""
from analyzer.score_adjuster import (  # explicit re-exports for IDE clarity
    ScoreAdjuster, ScoreAdjustment,
    count_clarifications_from_bus, count_missing_info,
)
