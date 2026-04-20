"""Context extraction: build per-agent context slices + read existing project code.

Maintain-mode toolkit:
  - ProjectInfo / detect_project : find and classify the user's project
  - build_scoped_context          : task-keyword-scoped file reading
  - GitHelper                     : branch, diff, commit management
  - HealthChecker                 : pre-flight analyze + test baseline
  - resolve_output_dir            : put outputs inside project (.multi_agent/)
"""
from .context_builder import ContextBuilder
from .project_context_reader import (
    read_project, save_context, detect_project_name,
)
from .project_detector import (
    ProjectInfo, detect_project, find_project_root, slugify_name,
)
from .scoped_reader import build_scoped_context, extract_keywords
from .git_helper import GitHelper, GitSnapshot, DiffReport
from .health_check import HealthChecker, HealthReport
from .output_paths import (
    resolve_output_dir, resolve_cache_dir, session_file,
    MULTI_AGENT_DIRNAME,
)
from .refresh import ContextRefresher, ContextWatermark

__all__ = [
    "ContextBuilder",
    "read_project", "save_context", "detect_project_name",
    "ProjectInfo", "detect_project", "find_project_root", "slugify_name",
    "build_scoped_context", "extract_keywords",
    "GitHelper", "GitSnapshot", "DiffReport",
    "HealthChecker", "HealthReport",
    "resolve_output_dir", "resolve_cache_dir", "session_file",
    "MULTI_AGENT_DIRNAME",
    "ContextRefresher", "ContextWatermark",
]
