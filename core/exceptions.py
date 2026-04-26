"""
Project-wide custom exceptions.

Rule: when a failure mode has a name (not "something went wrong" but
"agent CLI ran out of retries" / "checkpoint file corrupted"), give it a
class here. Catchers can then disambiguate.
"""
from __future__ import annotations


class PipelineError(Exception):
    """Base class for all pipeline-specific errors."""


class AgentCallError(PipelineError):
    """Raised by BaseAgent._call after exhausting retries."""

    def __init__(self, role: str, attempts: int, last_error: BaseException):
        self.role = role
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"[{role}] CLI failed after {attempts} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        )


class AgentCLINotFound(PipelineError):
    """`claude` binary is missing from PATH."""


class CheckpointCorrupt(PipelineError):
    """Session checkpoint file exists but cannot be parsed."""

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Checkpoint corrupt at {path}: {reason}")


class LearningDataSparse(PipelineError):
    """Not enough training data yet for the regression classifier."""

    def __init__(self, have: int, need: int):
        self.have = have
        self.need = need
        super().__init__(
            f"Learning data sparse: have {have} labelled samples, need {need}."
        )


class QuotaExceeded(PipelineError):
    """Token budget exhausted and user chose to stop."""


class ProfileMissing(PipelineError):
    """Requested profile does not exist on disk."""
