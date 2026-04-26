"""
Architecture compliance checker — enforces the rules documented in
CLAUDE.md §12. Run via:

    python -m scripts.check_architecture
    # or inside --doctor

Exit code 0 = clean, 1 = violations. Suitable for pre-commit hook or CI.

Rules checked
-------------
  §12.1  Package boundaries (with documented exceptions)
  §12.3  Orchestrator size (≤ 800 lines hard limit)
  §12.4  Paths via core.paths (no Path(__file__).parent.parent / "rules")
  §12.4  Logging via core.logging (no local tprint / _tprint definitions)
  §12.4  Env vars via core.config (no raw os.environ.get("MULTI_AGENT_*"))
  §12.4  Custom exceptions used (AgentCallError seen at least once)
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Project root = parent of this file's parent (scripts/ → project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Rule configuration ───────────────────────────────────────────────────────

# Package → (forbidden top-level packages, allow-list of specific modules,
# allow-list of specific filenames that bypass this rule)
BOUNDARY_RULES = [
    (
        "agents",
        {"pipeline", "learning", "analyzer", "session", "orchestrator", "testing"},
        {"pipeline.skill_selector", "pipeline.task_models",
         "pipeline.parsers"},   # pure text parsers — no orch state
        {"rule_optimizer_agent.py", "skill_designer_agent.py", "design_agent.py"},
    ),
    (
        "core",
        {"agents", "pipeline", "learning", "analyzer", "session", "context",
         "testing", "reporting", "orchestrator", "cli"},
        set(),
        set(),       # no exceptions — core/ is now strictly utility
    ),
    (
        "cli",
        set(),              # CLI aggregator may import anything (entry-point helper)
        set(), set(),
    ),
    (
        "session",
        {"pipeline", "learning", "analyzer", "agents", "orchestrator"},
        set(), set(),
    ),
    (
        "pipeline",
        {"orchestrator", "agents"},
        {"analyzer.score_adjuster", "learning.audit_log",
         "pipeline.skill_selector", "pipeline.task_models",
         # PM route types (data + dataclass) used by pm_router / clarification
         "agents.pm_agent"},
        set(),
    ),
    (
        "analyzer",
        {"orchestrator"},
        set(), set(),
    ),
    (
        "learning",
        {"orchestrator"},
        {"analyzer.score_adjuster", "pipeline.skill_selector",
         "pipeline.task_models"},
        set(),
    ),
    (
        "testing",
        {"orchestrator"},
        set(), set(),
    ),
]

ORCHESTRATOR_MAX_LINES = 800
LEARNING_RUNNERS_MAX_LINES = 500   # split target for runners.py

# Patterns that indicate a violation (grep-style)
PATH_VIOLATION_REGEX = r'Path\(__file__\)\.(?:resolve\(\)\.)?parent\.parent.*["\']rules["\']'
LOGGING_VIOLATION_REGEX = r"^(?:def tprint|def _tprint|_print_lock\s*=)"
ENV_VIOLATION_REGEX = r'os\.environ\.get\(\s*["\']MULTI_AGENT_'

# Files allowed to define these (the canonical source)
PATH_ALLOW = {"core/paths.py"}
LOG_ALLOW = {"core/logging.py"}
ENV_ALLOW = {"core/config.py"}

# §3 conventions — stdlib modules that must be imported at module top,
# never inside a function body.
HOIST_STDLIB_MODULES = {"re", "json", "os", "sys"}

# Packages whose .py files must use `tprint()` from core.logging rather than
# raw `print()`. These are the contexts where agents run in parallel and
# interleaved stdout is a real hazard. CLI aggregators, diagnostic tools,
# and reporting/health-check modules render serially and keep `print`.
TPRINT_REQUIRED_PATH_PREFIXES = (
    "agents/", "pipeline/", "learning/", "analyzer/", "testing/",
)
TPRINT_PRINT_ALLOW_FILES = {
    "core/logging.py",                # defines tprint, legitimately uses print
    "testing/stitch_browser.py",      # Playwright flow runs serially
}


# ── Violation types ──────────────────────────────────────────────────────────

@dataclass
class Violation:
    rule: str
    location: str
    detail: str


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)

    def add(self, rule: str, location: str, detail: str) -> None:
        self.violations.append(Violation(rule, location, detail))

    def ok(self) -> bool:
        return not self.violations

    def print_summary(self) -> None:
        if not self.violations:
            print("  ✅ Architecture compliance: all rules pass.")
            return
        print(f"\n  🔴 {len(self.violations)} architecture violation(s):\n")
        by_rule: dict[str, list[Violation]] = {}
        for v in self.violations:
            by_rule.setdefault(v.rule, []).append(v)
        for rule, items in by_rule.items():
            print(f"  [{rule}] — {len(items)} issue(s)")
            for v in items[:5]:
                print(f"    {v.location}: {v.detail}")
            if len(items) > 5:
                print(f"    ... {len(items) - 5} more")


# ── Individual checks ────────────────────────────────────────────────────────

def _iter_py(root: Path, pkg: str) -> list[Path]:
    """All .py files under a package, excluding caches and tests."""
    pkg_dir = root / pkg
    if not pkg_dir.exists():
        return []
    return [
        p for p in pkg_dir.rglob("*.py")
        if "__pycache__" not in p.parts and ".egg-info" not in str(p)
    ]


def check_boundaries(root: Path, report: Report) -> None:
    """§12.1 — package may not import from forbidden top-level packages."""
    for pkg, forbidden, allowed_modules, allowed_files in BOUNDARY_RULES:
        for f in _iter_py(root, pkg):
            if f.name in allowed_files:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                mod: str | None = None
                if isinstance(node, ast.ImportFrom):
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = node.names[0].name
                if not mod:
                    continue
                top = mod.split(".")[0]
                if top in forbidden and mod not in allowed_modules:
                    rel = f.relative_to(root)
                    report.add(
                        "§12.1 package boundary",
                        f"{rel}:{node.lineno}",
                        f"{pkg}/ imports {mod}",
                    )


def check_orchestrator_size(root: Path, report: Report) -> None:
    """§12.3 — orchestrator.py must not exceed size limit."""
    p = root / "orchestrator.py"
    if not p.exists():
        return
    n = sum(1 for _ in p.open(encoding="utf-8"))
    if n > ORCHESTRATOR_MAX_LINES:
        report.add(
            "§12.3 thin orchestrator",
            "orchestrator.py",
            f"{n} lines (max {ORCHESTRATOR_MAX_LINES})",
        )


def check_learning_runners_size(root: Path, report: Report) -> None:
    """Soft check — learning/runners.py shouldn't re-grow into a god file."""
    p = root / "learning" / "runners.py"
    if not p.exists():
        return
    n = sum(1 for _ in p.open(encoding="utf-8"))
    if n > LEARNING_RUNNERS_MAX_LINES:
        report.add(
            "§12.3 runners size",
            "learning/runners.py",
            f"{n} lines (soft max {LEARNING_RUNNERS_MAX_LINES})",
        )


def _grep_violations(root: Path, pattern: str, allow: set[str],
                      rule: str, detail_tmpl: str, report: Report,
                      use_ast: bool = False) -> None:
    regex = re.compile(pattern, re.MULTILINE)
    for f in root.rglob("*.py"):
        if ("__pycache__" in f.parts or ".egg-info" in str(f)
                or "tests" in f.parts or "scripts" in f.parts):
            continue
        rel = str(f.relative_to(root))
        if rel in allow:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in regex.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            report.add(rule, f"{rel}:{line_no}", detail_tmpl)


def check_paths(root: Path, report: Report) -> None:
    """§12.4 — no scattered Path(__file__).parent.parent / 'rules'."""
    _grep_violations(
        root, PATH_VIOLATION_REGEX, PATH_ALLOW,
        "§12.4 paths via core.paths",
        "raw Path(__file__).parent / 'rules' — use core.paths.RULES_DIR",
        report,
    )


def check_logging(root: Path, report: Report) -> None:
    """§12.4 — no local tprint / _tprint definitions."""
    _grep_violations(
        root, LOGGING_VIOLATION_REGEX, LOG_ALLOW,
        "§12.4 logging via core.logging",
        "local tprint/_tprint defined — import from core.logging",
        report,
    )


def check_env_vars(root: Path, report: Report) -> None:
    """§12.4 — no raw os.environ.get('MULTI_AGENT_*')."""
    _grep_violations(
        root, ENV_VIOLATION_REGEX, ENV_ALLOW,
        "§12.4 env via core.config",
        "raw os.environ.get('MULTI_AGENT_*') — use core.config.get_bool/get_int",
        report,
    )


def check_no_inline_stdlib_imports(root: Path, report: Report) -> None:
    """§3 — stdlib imports (re/json/os/sys) must be at module top, not
    inline inside a function body. Inline imports both re-execute every
    call AND hide dependencies from linters + tooling."""
    for f in root.rglob("*.py"):
        if ("__pycache__" in f.parts or ".egg-info" in str(f)
                or "tests" in f.parts):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    modname = sub.names[0].name.split(".")[0] if sub.names else ""
                elif isinstance(sub, ast.ImportFrom):
                    modname = (sub.module or "").split(".")[0]
                else:
                    continue
                if modname in HOIST_STDLIB_MODULES:
                    rel = f.relative_to(root)
                    report.add(
                        "§3 hoist stdlib imports",
                        f"{rel}:{sub.lineno}",
                        f"inline `import {modname}` inside def {node.name}() — hoist to top",
                    )


def check_no_raw_print(root: Path, report: Report) -> None:
    """§3 — use `tprint()` from core.logging in parallel-execution contexts.
    Raw `print()` interleaves between threads. Exception list in
    TPRINT_PRINT_ALLOW_FILES and entire packages outside the prefix tuple
    (cli/, reporting/, scripts/, context/, core/, session/) can use print.
    """
    print_re = re.compile(r"^\s*print\(")
    for f in root.rglob("*.py"):
        if ("__pycache__" in f.parts or ".egg-info" in str(f)
                or "tests" in f.parts):
            continue
        rel = str(f.relative_to(root))
        if not rel.startswith(TPRINT_REQUIRED_PATH_PREFIXES):
            continue
        if rel in TPRINT_PRINT_ALLOW_FILES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if print_re.match(line):
                report.add(
                    "§3 tprint over print",
                    f"{rel}:{i}",
                    "raw print() in parallel-execution code — use core.logging.tprint",
                )


def check_custom_exceptions_used(root: Path, report: Report) -> None:
    """§12.4 — core.exceptions should not be dead code."""
    hits = 0
    for f in root.rglob("*.py"):
        if "__pycache__" in f.parts or f.name == "exceptions.py":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "AgentCallError" in text or "core.exceptions" in text:
            hits += 1
    if hits == 0:
        report.add(
            "§12.4 custom exceptions",
            "core/exceptions.py",
            "defined but never imported — dead code",
        )


# ── §13: pyflakes — undefined names + unused imports ────────────────────────

# Files exempt from undefined-name check (rare false-positive cases).
_PYFLAKES_FILE_ALLOW: set[str] = set()
# Substrings — if any appears in a pyflakes message, suppress it.
_PYFLAKES_MSG_SUPPRESS = (
    # `from __future__ import annotations` makes type hints stringified;
    # pyflakes still flags forward refs in `Optional['Foo']`. Common in
    # this codebase. Real undefined NAMES (used at runtime) still flagged.
    "may be undefined, or defined from star imports",
    # Forward-ref string annotations — pyflakes still parses inside the
    # quotes even with `from __future__ import annotations`. Names listed
    # here are imported under TYPE_CHECKING in their owning files.
    "undefined name 'RouteDecision'",
    "undefined name 'TaskMetadata'",
)


def check_undefined_names_and_unused_imports(root: Path, report: Report) -> None:
    """§13 — run pyflakes across the project and surface:
      - undefined name (real bug — we hit 5 today after refactors)
      - unused import (drift after move/rename)
      - star imports (mask undefined names)

    pyflakes is a pure-Python lint, fast (~ms per file). Skip if the lib
    isn't installed (CI warns once); make pyflakes a hard dep in
    requirements-dev so it's always available.
    """
    try:
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter
    except ImportError:
        report.add(
            "§13 lint dep",
            "(pyflakes not installed)",
            "install with `pip install pyflakes` for lint enforcement",
        )
        return

    import io
    skip_dirs = {"__pycache__", ".egg-info", "tests", "backups"}

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        if str(rel) in _PYFLAKES_FILE_ALLOW:
            continue

        # Detect back-compat shim modules (intentional re-exports). Top-of-file
        # docstring mentions "shim" / "re-export" / "back-compat" → skip
        # unused-import check (the "unused" imports are the public API).
        try:
            head = path.read_text(encoding="utf-8")[:400].lower()
        except OSError:
            head = ""
        is_shim = any(token in head for token in
                      ("back-compat shim", "back compat shim", "re-export",
                       "shim — ", "shim - ", "shim—"))

        # Capture pyflakes output instead of letting it write to stderr.
        out, err = io.StringIO(), io.StringIO()
        try:
            checkPath(str(path), Reporter(out, err))
        except (OSError, SyntaxError):
            continue

        for line in (out.getvalue() + err.getvalue()).splitlines():
            line = line.strip()
            if not line:
                continue
            if any(s in line for s in _PYFLAKES_MSG_SUPPRESS):
                continue

            # Hard-fail conditions
            is_undefined = "undefined name" in line
            is_unused_import = "imported but unused" in line
            is_star_import = "used; unable to detect" in line

            # __init__.py re-export pattern — `unused import` here is
            # usually intentional re-export. Only flag if line lists an
            # explicit `as` alias suggesting unused.
            if is_unused_import and rel.name == "__init__.py":
                continue
            # Back-compat shim modules — imports are public re-exports.
            if is_unused_import and is_shim:
                continue

            if is_undefined:
                rule = "§13 undefined name"
            elif is_unused_import:
                rule = "§13 unused import"
            elif is_star_import:
                rule = "§13 star import"
            else:
                continue

            # Format: `path:lineno: message`
            parts = line.split(":", 2)
            if len(parts) >= 3:
                location = f"{parts[0]}:{parts[1]}"
                detail = parts[2].strip()
            else:
                location = str(rel)
                detail = line
            report.add(rule, location, detail)


# ── Public entry point ───────────────────────────────────────────────────────

def audit(root: Path | None = None) -> Report:
    """Run all checks and return a Report."""
    root = root or PROJECT_ROOT
    report = Report()
    check_boundaries           (root, report)
    check_orchestrator_size    (root, report)
    check_learning_runners_size(root, report)
    check_paths                (root, report)
    check_logging              (root, report)
    check_env_vars             (root, report)
    check_no_inline_stdlib_imports(root, report)
    check_no_raw_print         (root, report)
    check_custom_exceptions_used(root, report)
    check_undefined_names_and_unused_imports(root, report)
    return report


def main() -> int:
    report = audit()
    report.print_summary()
    return 0 if report.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
