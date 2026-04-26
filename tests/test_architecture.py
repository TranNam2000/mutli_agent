"""Run the architecture checker — treat any violation as a test failure."""
import core.paths as paths

from scripts.check_architecture import (
    audit, check_no_inline_stdlib_imports, check_no_raw_print, Report,
)


def test_architecture_compliance():
    report = audit()
    if report.violations:
        msg = "\n".join(
            f"[{v.rule}] {v.location}: {v.detail}" for v in report.violations
        )
        assert False, f"Architecture violations:\n{msg}"


def test_orchestrator_under_size_limit():
    orch = paths.PROJECT_ROOT / "orchestrator.py"
    assert orch.exists()
    lines = sum(1 for _ in orch.open(encoding="utf-8"))
    # CLAUDE.md §12.3 — strict target ≤ 800
    assert lines <= 800, f"orchestrator.py has {lines} lines, exceeds 800"


# ── New rule checks ─────────────────────────────────────────────────────────

def test_check_no_inline_stdlib_imports_catches_violation(tmp_path):
    """The checker must flag `import re` placed inside a function body."""
    # Mimic the package layout check_no_inline_stdlib_imports expects.
    (tmp_path / "agents").mkdir()
    bad = tmp_path / "agents" / "evil.py"
    bad.write_text(
        "def f():\n"
        "    import re          # should be hoisted\n"
        "    return re.compile('x')\n",
        encoding="utf-8",
    )
    report = Report()
    check_no_inline_stdlib_imports(tmp_path, report)
    assert any("hoist stdlib" in v.rule for v in report.violations)
    assert any("evil.py" in v.location for v in report.violations)


def test_check_no_inline_stdlib_imports_allows_top_level(tmp_path):
    """Top-level imports must not be flagged."""
    (tmp_path / "agents").mkdir()
    good = tmp_path / "agents" / "fine.py"
    good.write_text(
        "import re\n"
        "def f():\n"
        "    return re.compile('x')\n",
        encoding="utf-8",
    )
    report = Report()
    check_no_inline_stdlib_imports(tmp_path, report)
    assert not report.violations


def test_check_no_raw_print_catches_in_agents(tmp_path):
    """A raw `print(` in agents/ must be flagged."""
    (tmp_path / "agents").mkdir()
    bad = tmp_path / "agents" / "loud.py"
    bad.write_text(
        "def f():\n"
        "    print('hi')\n",
        encoding="utf-8",
    )
    report = Report()
    check_no_raw_print(tmp_path, report)
    assert any("tprint over print" in v.rule for v in report.violations)


def test_check_no_raw_print_allows_cli_dir(tmp_path):
    """`print(` in cli/ is allowed — it's the CLI aggregator layer."""
    (tmp_path / "cli").mkdir()
    ok = tmp_path / "cli" / "x.py"
    ok.write_text("print('user-facing')\n", encoding="utf-8")

    report = Report()
    check_no_raw_print(tmp_path, report)
    assert not report.violations


def test_check_no_raw_print_allows_self_print_methods(tmp_path):
    """A method named self.print_review must NOT be flagged as raw print."""
    (tmp_path / "agents").mkdir()
    ok = tmp_path / "agents" / "x.py"
    ok.write_text(
        "def f(self):\n"
        "    self.print_review()\n"
        "    result.print_summary()\n",
        encoding="utf-8",
    )
    report = Report()
    check_no_raw_print(tmp_path, report)
    assert not report.violations
