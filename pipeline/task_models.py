"""
Task models for the task-based pipeline flow.

BA produces a structured task list which flows through:
  BA (classify) →
    UI tasks  → Design (find or create) → back to BA → TechLead
    Logic tasks → TechLead
  TechLead (prioritize by resources) →
    Dev (parallel per capacity)
    Test (plan in same priority order)

Task markdown format (parseable):

  ## TASK-001 | type=ui | priority=P0 | module=auth | complexity=M | risk=high
  **Title:** Login screen with Google OAuth
  **Description:** ...
  **AC:**
    - GIVEN ... WHEN ... THEN ...
  **Dependencies:** TASK-XXX, TASK-YYY
  **Design ref:** (populated by Design agent)
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum


class TaskType(str, Enum):
    UI     = "ui"
    LOGIC  = "logic"
    BUG    = "bug"
    HOTFIX = "hotfix"
    MIXED  = "mixed"   # has both UI and logic — routes through both lanes


class Priority(str, Enum):
    P0 = "P0"   # blocker / must-have this sprint
    P1 = "P1"   # should-have
    P2 = "P2"   # nice-to-have
    P3 = "P3"   # backlog


class Complexity(str, Enum):
    S = "S"    # < 4 hours
    M = "M"    # 4-12 hours
    L = "L"    # 1-3 days
    XL = "XL"  # > 3 days — should be split


class Risk(str, Enum):
    LOW    = "low"
    MED    = "med"
    HIGH   = "high"


class BusinessValue(str, Enum):
    """How much business impact the task delivers."""
    CRITICAL = "critical"  # revenue-blocking / must ship
    HIGH     = "high"      # key KPI driver
    NORMAL   = "normal"    # default
    LOW      = "low"       # nice polish, no KPI impact


_PRIORITY_WEIGHT = {"P0": 10, "P1": 6, "P2": 3, "P3": 1}
_COMPLEXITY_HOURS = {"S": 3, "M": 8, "L": 18, "XL": 40}
_RISK_MULTIPLIER = {"low": 1.0, "med": 1.3, "high": 1.7}
_BUSINESS_VALUE_BOOST = {"critical": 1.8, "high": 1.3, "normal": 1.0, "low": 0.6}


@dataclass
class Task:
    id:           str
    type:         TaskType
    priority:     Priority
    module:       str = ""
    title:        str = ""
    description:  str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    complexity:   Complexity = Complexity.M
    risk:         Risk = Risk.LOW
    business_value: BusinessValue = BusinessValue.NORMAL  # impact on KPI/revenue
    design_ref:   str = ""           # populated by Design agent
    assigned_to:  str = ""           # populated by TechLead
    sprint:       int = 0            # 0 = backlog, 1+ = scheduled
    status:       str = "new"        # new | designed | planned | in_dev | tested | done
    # ── NEW: structured metadata drives Critic-skip decisions + audit log.
    # When None, caller should call learning.task_metadata.derive_from_task(t).
    metadata:     "TaskMetadata | None" = None

    def get_metadata(self):
        """Return metadata, deriving a default one from legacy fields on first access."""
        if self.metadata is None:
            from .task_metadata import derive_from_task
            self.metadata = derive_from_task(self)
        return self.metadata

    @property
    def estimated_hours(self) -> float:
        return _COMPLEXITY_HOURS[self.complexity.value]

    @property
    def priority_score(self) -> float:
        """Higher = do first. (priority + business_value) × risk / √hours."""
        pw    = _PRIORITY_WEIGHT[self.priority.value]
        bv    = _BUSINESS_VALUE_BOOST[self.business_value.value]
        rm    = _RISK_MULTIPLIER[self.risk.value]
        hours = max(self.estimated_hours, 1)
        return pw * bv * rm / (hours ** 0.5)

    @property
    def needs_design(self) -> bool:
        return self.type in (TaskType.UI, TaskType.MIXED)

    @property
    def needs_logic(self) -> bool:
        return self.type in (TaskType.LOGIC, TaskType.BUG, TaskType.HOTFIX, TaskType.MIXED)

    def to_markdown(self) -> str:
        lines = [
            f"## {self.id} | type={self.type.value} | priority={self.priority.value} "
            f"| module={self.module} | complexity={self.complexity.value} "
            f"| risk={self.risk.value} | value={self.business_value.value}",
            f"**Title:** {self.title}",
            f"**Description:** {self.description}",
        ]
        if self.acceptance_criteria:
            lines.append("**AC:**")
            for ac in self.acceptance_criteria:
                lines.append(f"  - {ac}")
        if self.dependencies:
            lines.append(f"**Dependencies:** {', '.join(self.dependencies)}")
        if self.design_ref:
            lines.append(f"**Design ref:** {self.design_ref}")
        if self.assigned_to:
            lines.append(f"**Assigned:** {self.assigned_to} (sprint {self.sprint})")
        # Structured metadata block — consumed by orchestrator / audit log.
        if self.metadata is not None:
            from .task_metadata import render_meta_block
            lines.append("")
            lines.append(render_meta_block(self.metadata))
        return "\n".join(lines)


# ── Parser ────────────────────────────────────────────────────────────────────

_HEADER_RE = re.compile(
    r"^##\s+(TASK-[\w\-]+)"
    r"\s*\|\s*type=([\w]+)"
    r"\s*\|\s*priority=(P[0-3])"
    r"(?:\s*\|\s*module=([^|]+))?"
    r"(?:\s*\|\s*complexity=(XL|[SML]))?"
    r"(?:\s*\|\s*risk=(\w+))?"
    r"(?:\s*\|\s*value=(\w+))?",
    re.IGNORECASE,
)


def parse_tasks(markdown: str) -> list[Task]:
    """Extract Task objects from BA-produced markdown."""
    tasks: list[Task] = []
    current: Task | None = None
    in_ac = False

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        header = _HEADER_RE.match(line)
        if header:
            if current:
                tasks.append(current)
            tid, tp, pr, mod, cx, rk, bv = header.groups()
            current = Task(
                id=tid,
                type=TaskType(tp.lower()) if tp.lower() in [e.value for e in TaskType] else TaskType.LOGIC,
                priority=Priority(pr.upper()),
                module=(mod or "").strip(),
                complexity=Complexity(cx.upper()) if cx and cx.upper() in [e.value for e in Complexity] else Complexity.M,
                risk=Risk(rk.lower()) if rk and rk.lower() in [e.value for e in Risk] else Risk.LOW,
                business_value=(BusinessValue(bv.lower()) if bv and bv.lower() in [e.value for e in BusinessValue]
                                else BusinessValue.NORMAL),
            )
            in_ac = False
            continue

        if not current:
            continue

        stripped = line.strip()
        if stripped.startswith("**Title:**"):
            current.title = stripped.split("**Title:**", 1)[1].strip()
            in_ac = False
        elif stripped.startswith("**Description:**"):
            current.description = stripped.split("**Description:**", 1)[1].strip()
            in_ac = False
        elif stripped.startswith("**AC:**"):
            in_ac = True
        elif stripped.startswith("**Dependencies:**"):
            deps = stripped.split("**Dependencies:**", 1)[1].strip()
            current.dependencies = [d.strip() for d in deps.split(",") if d.strip() and d.strip() != "-"]
            in_ac = False
        elif stripped.startswith("**Design ref:**"):
            current.design_ref = stripped.split("**Design ref:**", 1)[1].strip()
            in_ac = False
        elif stripped.startswith("**Assigned:**"):
            current.assigned_to = stripped.split("**Assigned:**", 1)[1].strip()
            in_ac = False
        elif in_ac and stripped.startswith("-"):
            current.acceptance_criteria.append(stripped.lstrip("-* ").strip())

    if current:
        tasks.append(current)

    # Second pass: attach any `json META` block embedded in each task body.
    _attach_metadata_blocks(tasks, markdown)
    return tasks


def _attach_metadata_blocks(tasks: list[Task], markdown: str) -> None:
    """Split markdown by task headers; try to pull a META block out of each body."""
    if not tasks:
        return
    from .task_metadata import extract_meta_block, derive_from_task

    # Build a lookup by id so we can attach regardless of ordering.
    by_id = {t.id: t for t in tasks}

    # Split markdown into per-task bodies — delimiter is the task header line.
    chunks: list[tuple[str, str]] = []   # (task_id, body)
    current_id: str | None = None
    current_body: list[str] = []
    for line in markdown.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            if current_id:
                chunks.append((current_id, "\n".join(current_body)))
            current_id = m.group(1)
            current_body = [line]
        else:
            current_body.append(line)
    if current_id:
        chunks.append((current_id, "\n".join(current_body)))

    for tid, body in chunks:
        task = by_id.get(tid)
        if task is None:
            continue
        meta = extract_meta_block(body)
        task.metadata = meta if meta is not None else derive_from_task(task)


# ── Prioritization & topological ordering ────────────────────────────────────

def topo_order(tasks: list[Task]) -> list[Task]:
    """Return tasks in dependency-respecting order, ties broken by priority_score."""
    task_by_id = {t.id: t for t in tasks}
    visited: set[str] = set()
    result: list[Task] = []

    def visit(t: Task):
        if t.id in visited:
            return
        visited.add(t.id)
        # Visit deps first
        for dep_id in t.dependencies:
            dep = task_by_id.get(dep_id)
            if dep and dep.id not in visited:
                visit(dep)
        result.append(t)

    # Seed with highest priority tasks
    for t in sorted(tasks, key=lambda x: -x.priority_score):
        visit(t)
    return result


def split_by_type(tasks: list[Task]) -> dict[str, list[Task]]:
    """Split into {ui, logic, bug, hotfix} lists. Mixed goes into both 'ui' and 'logic'."""
    out: dict[str, list[Task]] = {"ui": [], "logic": [], "bug": [], "hotfix": []}
    for t in tasks:
        if t.type == TaskType.MIXED:
            out["ui"].append(t)
            out["logic"].append(t)
        elif t.type.value in out:
            out[t.type.value].append(t)
    return out


def expand_mixed_tasks(tasks: list[Task]) -> tuple[list[Task], list[tuple[str, str]]]:
    """
    Split every MIXED task into 2 child tasks: `<id>-ui` + `<id>-logic`.
    Parent is kept as an umbrella (status="parent") but excluded from scheduling.

    Returns (expanded_tasks, links) where links = [(parent_id, child_id), ...]

    Benefits:
      - UI child goes to Design only (no logic design waste)
      - Logic child goes straight to TechLead (no design delay)
      - Dependencies on parent auto-rewired: logic-child depends on ui-child
    """
    expanded: list[Task] = []
    links: list[tuple[str, str]] = []
    id_map: dict[str, list[str]] = {}  # parent_id → [child_ids]

    for t in tasks:
        if t.type != TaskType.MIXED:
            expanded.append(t)
            continue

        ui_id    = f"{t.id}-ui"
        logic_id = f"{t.id}-logic"
        ui_child = Task(
            id=ui_id, type=TaskType.UI,
            priority=t.priority, module=t.module,
            title=f"[UI] {t.title}",
            description=f"UI portion of {t.id}. " + t.description,
            acceptance_criteria=[c for c in t.acceptance_criteria
                                 if any(kw in c.lower() for kw in
                                        ("màn", "screen", "button", "layout", "ui",
                                         "current", "show", "display", "tap", "toast"))]
                                 or t.acceptance_criteria[: len(t.acceptance_criteria) // 2],
            dependencies=list(t.dependencies),
            complexity=Complexity.S if t.complexity == Complexity.M else Complexity.M,
            risk=t.risk,
        )
        logic_child = Task(
            id=logic_id, type=TaskType.LOGIC,
            priority=t.priority, module=t.module,
            title=f"[Logic] {t.title}",
            description=f"Logic portion of {t.id}. " + t.description,
            acceptance_criteria=[c for c in t.acceptance_criteria
                                  if any(kw in c.lower() for kw in
                                         ("api", "data", "validate", "save", "fetch",
                                          "error", "loading", "state", "cache"))]
                                  or t.acceptance_criteria[len(t.acceptance_criteria) // 2 :],
            dependencies=list(t.dependencies) + [ui_id],  # logic waits for UI scaffold
            complexity=Complexity.S if t.complexity == Complexity.M else Complexity.M,
            risk=t.risk,
        )
        # Parent — kept for reference
        t.status = "parent"
        expanded.append(ui_child)
        expanded.append(logic_child)
        links.append((t.id, ui_id))
        links.append((t.id, logic_id))
        id_map[t.id] = [ui_id, logic_id]

    # Rewire any task that depended on a parent MIXED → depend on both children
    for t in expanded:
        new_deps: list[str] = []
        for dep in t.dependencies:
            if dep in id_map:
                new_deps.extend(id_map[dep])
            else:
                new_deps.append(dep)
        t.dependencies = new_deps

    return expanded, links


# ── Resource-aware sprint assignment ─────────────────────────────────────────

@dataclass
class Resources:
    dev_slots:      int = 2         # number of parallel devs
    sprint_hours:   int = 80        # per-dev hours per sprint
    sprints_ahead:  int = 3         # how many sprints to plan


@dataclass
class SprintPlan:
    sprints: list[list[Task]] = field(default_factory=list)  # sprints[i] = tasks in sprint i+1
    unassigned: list[Task] = field(default_factory=list)     # didn't fit → backlog

    def summary(self) -> str:
        out = []
        for i, bucket in enumerate(self.sprints, 1):
            total_h = sum(t.estimated_hours for t in bucket)
            out.append(f"  Sprint {i}: {len(bucket)} tasks ({total_h:.0f}h)")
            for t in bucket:
                out.append(f"    [{t.priority.value}] {t.id} · {t.type.value} · "
                           f"{t.complexity.value} · {t.assigned_to or '-'} — {t.title[:50]}")
        if self.unassigned:
            out.append(f"  Backlog: {len(self.unassigned)} tasks")
            for t in self.unassigned[:5]:
                out.append(f"    - {t.id} · {t.title[:60]}")
        return "\n".join(out)


def plan_sprints(tasks: list[Task], resources: Resources) -> SprintPlan:
    """
    Bin-packing per sprint:
      - Walk tasks in topological + priority order
      - Pack into sprint buckets, respecting per-dev hour capacity
      - Round-robin assignment across dev slots
    """
    ordered = topo_order(tasks)
    plan = SprintPlan(sprints=[[] for _ in range(resources.sprints_ahead)])
    # track hours per (sprint_index, dev_slot)
    usage: dict[tuple[int, int], float] = {}

    for task in ordered:
        placed = False
        for sprint_idx in range(resources.sprints_ahead):
            for dev_slot in range(resources.dev_slots):
                key = (sprint_idx, dev_slot)
                used = usage.get(key, 0.0)
                # Check dependency: all deps must be in same or earlier sprint
                deps_ok = all(
                    any(d.id == dep_id for s in plan.sprints[: sprint_idx + 1] for d in s)
                    for dep_id in task.dependencies
                    if any(t.id == dep_id for t in tasks)  # skip external deps
                )
                if not deps_ok:
                    continue
                if used + task.estimated_hours <= resources.sprint_hours:
                    usage[key] = used + task.estimated_hours
                    task.assigned_to = f"dev_{dev_slot + 1}"
                    task.sprint = sprint_idx + 1
                    task.status = "planned"
                    plan.sprints[sprint_idx].append(task)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            plan.unassigned.append(task)

    # Trim empty trailing sprints
    while plan.sprints and not plan.sprints[-1]:
        plan.sprints.pop()

    return plan


# ── Display ───────────────────────────────────────────────────────────────────

def format_task_list(tasks: list[Task], *, show_score: bool = False) -> str:
    if not tasks:
        return "  (no tasks)"
    rows = [
        ("ID", "Type", "Pri", "Cx", "Risk", "Hours", "Module", "Title"),
    ]
    for t in tasks:
        rows.append((
            t.id, t.type.value, t.priority.value,
            t.complexity.value, t.risk.value,
            f"{t.estimated_hours:.0f}h",
            (t.module or "-")[:15],
            t.title[:50],
        ))
    # Compute col widths
    widths = [max(len(str(r[c])) for r in rows) for c in range(len(rows[0]))]
    lines = []
    for r in rows:
        lines.append("  " + "  ".join(str(r[c]).ljust(widths[c]) for c in range(len(r))))
    if show_score:
        lines.append("  " + "─" * 60)
        for t in sorted(tasks, key=lambda x: -x.priority_score)[:5]:
            lines.append(f"  top score: {t.id} = {t.priority_score:.2f}")
    return "\n".join(lines)
