"""
Back-compat shim — TaskMetadata moved to pipeline.task_metadata so it lives
next to Task (pipeline.task_models) and doesn't force pipeline to import
learning (forbidden by §12.2 dependency direction).

Everything here is re-exported from pipeline.task_metadata. New code should
import from pipeline.task_metadata directly.
"""
from __future__ import annotations

from pipeline.task_metadata import (  # noqa: F401
    Context,
    FlowControl,
    TaskMetadata,
    TechnicalDebt,
    derive_from_task,
    extract_meta_block,
    render_meta_block,
)
