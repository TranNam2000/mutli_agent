"""Multi-Agent Product Development Pipeline.

Entry points:
  - CLI:       python -m multi_agent  (via main.py)
  - Library:   from multi_agent import ProductDevelopmentOrchestrator

The codebase uses flat imports (e.g. `from agents import ...`) so it can run
standalone from inside the folder. To make `from multi_agent import X` work
when imported as a package from the parent directory, we add our own folder
to sys.path on package init.
"""
import sys as _sys
from pathlib import Path as _Path
_PKG_DIR = str(_Path(__file__).resolve().parent)
if _PKG_DIR not in _sys.path:
    _sys.path.insert(0, _PKG_DIR)

from orchestrator import ProductDevelopmentOrchestrator
from agents import (
    BAAgent, DesignAgent, TechLeadAgent, DevAgent, TestAgent,
    CriticAgent, InvestigationAgent, RuleOptimizerAgent, SkillDesignerAgent,
)
from learning import select_skill, detect_scope, SkillOptimizer
from testing import PatrolRunner, MaestroRunner, AutoFeedback
from reporting import build_report

__version__ = "0.2.0"

__all__ = [
    "ProductDevelopmentOrchestrator",
    "BAAgent", "DesignAgent", "TechLeadAgent", "DevAgent", "TestAgent",
    "CriticAgent", "InvestigationAgent", "RuleOptimizerAgent", "SkillDesignerAgent",
    "select_skill", "detect_scope", "SkillOptimizer",
    "PatrolRunner", "MaestroRunner", "AutoFeedback",
    "build_report",
]
