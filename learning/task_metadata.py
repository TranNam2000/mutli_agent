"""
TaskMetadata — the "nervous system" that carries structured signals
between agents so the orchestrator can make skip-Critic decisions,
and RuleOptimizer can learn from false-negatives.

Layout (per the pipeline spec):

{
  "task_id": "BUG-12345",
  "context": {
    "scope":      "feature | bug_fix | hotfix | refactor",
    "priority":   "P0 | P1 | P2 | P3",
    "risk_level": "low | med | high",
    "complexity": "S | M | L | XL"
  },
  "flow_control": {
    "skip_critic":   ["PM", "BA", "TechLead"],
    "require_qa":    true,
    "max_revisions": 2
  },
  "technical_debt": {
    "impact_area":     ["ui", "state_management", "api"],
    "legacy_affected": false
  }
}

Each role tops up the metadata as the task walks the pipeline:
  PM       → context.priority + business_value hint
  BA       → context.scope + functional_reqs snippet
  TechLead → context.complexity + context.risk_level + technical_debt.impact_area
  Dev      → technical_debt (files changed, LOC)
  QA       → outcome fields consumed by RuleOptimizer
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict


_VALID_SCOPE      = {"feature", "bug_fix", "hotfix", "refactor",
                     "ui_tweak", "investigation"}
_VALID_PRIORITY   = {"P0", "P1", "P2", "P3"}
_VALID_RISK       = {"low", "med", "high"}
_VALID_COMPLEXITY = {"S", "M", "L", "XL"}
# Roles that can appear in skip_critic.
_VALID_ROLES      = {"PM", "BA", "TechLead", "Design", "Dev", "QA"}


@dataclass
class Context:
    scope:      str = "feature"
    priority:   str = "P2"
    risk_level: str = "low"
    complexity: str = "M"


@dataclass
class FlowControl:
    skip_critic:   list[str] = field(default_factory=list)
    require_qa:    bool       = True
    max_revisions: int        = 2


@dataclass
class TechnicalDebt:
    impact_area:     list[str] = field(default_factory=list)
    legacy_affected: bool       = False


@dataclass
class TaskMetadata:
    task_id:       str
    context:       Context        = field(default_factory=Context)
    flow_control:  FlowControl    = field(default_factory=FlowControl)
    technical_debt: TechnicalDebt = field(default_factory=TechnicalDebt)

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "context": asdict(self.context),
            "flow_control": asdict(self.flow_control),
            "technical_debt": asdict(self.technical_debt),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskMetadata":
        d = data or {}
        ctx = d.get("context", {}) or {}
        fc  = d.get("flow_control", {}) or {}
        td  = d.get("technical_debt", {}) or {}

        # Validate & coerce — unknown values fall back to safe defaults
        # rather than raising, so legacy sessions don't crash on resume.
        scope = str(ctx.get("scope", "feature")).lower()
        if scope not in _VALID_SCOPE:
            scope = "feature"
        priority = str(ctx.get("priority", "P2")).upper()
        if priority not in _VALID_PRIORITY:
            priority = "P2"
        risk = str(ctx.get("risk_level", "low")).lower()
        if risk not in _VALID_RISK:
            risk = "low"
        complexity = str(ctx.get("complexity", "M")).upper()
        if complexity not in _VALID_COMPLEXITY:
            complexity = "M"

        skip_list = [str(r) for r in fc.get("skip_critic", []) if r in _VALID_ROLES]

        return cls(
            task_id=str(d.get("task_id", "TASK-UNKNOWN")),
            context=Context(scope, priority, risk, complexity),
            flow_control=FlowControl(
                skip_critic=skip_list,
                require_qa=bool(fc.get("require_qa", True)),
                max_revisions=int(fc.get("max_revisions", 2)),
            ),
            technical_debt=TechnicalDebt(
                impact_area=[str(a).lower() for a in td.get("impact_area", [])],
                legacy_affected=bool(td.get("legacy_affected", False)),
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> "TaskMetadata":
        return cls.from_dict(json.loads(raw))

    # ── Decision helpers (used by orchestrator) ──────────────────────────────

    def should_skip_critic(self, role: str) -> bool:
        """True if this role's Critic can be skipped for this task."""
        return role in self.flow_control.skip_critic

    def is_low_risk_small(self) -> bool:
        """Canonical "safe to skip Critic for PM/BA/TL" predicate."""
        return (self.context.complexity == "S" and
                self.context.risk_level == "low")

    def is_hot_p0(self) -> bool:
        """Hotfix P0 — skip intermediate Critic, keep Dev+QA."""
        return (self.context.scope == "hotfix" and
                self.context.priority == "P0")

    def touches_core(self) -> bool:
        """True if impact_area mentions core concerns — forces Critic for TL."""
        return any(a in ("core", "payment", "auth", "security")
                   for a in self.technical_debt.impact_area)


# ── Derivation from legacy Task fields ────────────────────────────────────────

def derive_from_task(task) -> TaskMetadata:
    """
    Build a sane TaskMetadata from an existing Task dataclass when the
    caller (e.g. old BA output) didn't embed a META block.

    This is the backward-compat bridge — every piece of code that needs
    metadata can call this and get a working object.
    """
    # Map TaskType → scope.
    type_to_scope = {
        "ui":     "ui_tweak",
        "logic":  "feature",
        "bug":    "bug_fix",
        "hotfix": "hotfix",
        "mixed":  "feature",
    }
    scope = type_to_scope.get(getattr(task.type, "value", str(task.type)).lower(),
                               "feature")

    risk_map = {"low": "low", "med": "med", "medium": "med",
                "high": "high"}
    risk = risk_map.get(getattr(task.risk, "value", str(task.risk)).lower(), "low")

    complexity = getattr(task.complexity, "value", str(task.complexity)).upper()
    if complexity not in _VALID_COMPLEXITY:
        complexity = "M"

    priority = getattr(task.priority, "value", str(task.priority)).upper()
    if priority not in _VALID_PRIORITY:
        priority = "P2"

    # Start with empty skip list — the orchestrator's decision engine will
    # compute effective skip set at runtime from (scope, risk, complexity).
    return TaskMetadata(
        task_id=task.id,
        context=Context(scope=scope, priority=priority,
                        risk_level=risk, complexity=complexity),
        flow_control=FlowControl(),
        technical_debt=TechnicalDebt(
            impact_area=[task.module.lower()] if task.module else [],
            legacy_affected=False,
        ),
    )


# ── Markdown block helpers (for embedding in task markdown) ──────────────────

_META_BLOCK_START = "```json META"
_META_BLOCK_END   = "```"


def render_meta_block(meta: TaskMetadata) -> str:
    """Emit the ```json META { ... } ``` fenced block for task markdown."""
    return f"{_META_BLOCK_START}\n{meta.to_json()}\n{_META_BLOCK_END}"


def extract_meta_block(task_body: str) -> TaskMetadata | None:
    """Pull the ```json META …``` block out of a single task's markdown body."""
    import re as _re
    pattern = _re.compile(
        r"```json\s+META\s*\n(.*?)\n```",
        _re.DOTALL,
    )
    m = pattern.search(task_body)
    if not m:
        return None
    try:
        return TaskMetadata.from_json(m.group(1))
    except Exception:
        return None
