"""
Pre-flight project health check for maintain mode.

Captures baseline BEFORE pipeline starts so we can distinguish:
  - "pre-existing failures that already existed" (don't blame Dev)
  - "failures introduced by this pipeline run" (blame Dev, block merge)

Supports: flutter analyze, dart analyze, npm run lint, pyright/ruff,
          existing unit tests.
"""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field

from .project_detector import ProjectInfo


@dataclass
class HealthReport:
    project_kind:      str
    analyze_errors:    int = 0
    analyze_warnings:  int = 0
    test_passed:       int = 0
    test_failed:       int = 0
    raw_logs:          dict[str, str] = field(default_factory=dict)
    baseline_issues:   set[str] = field(default_factory=set)  # stable IDs of pre-existing issues

    @property
    def healthy(self) -> bool:
        return self.analyze_errors == 0 and self.test_failed == 0

    @property
    def status_line(self) -> str:
        if self.healthy:
            return "✅ Project healthy"
        parts = []
        if self.analyze_errors:
            parts.append(f"{self.analyze_errors} analyze errors")
        if self.analyze_warnings:
            parts.append(f"{self.analyze_warnings} warnings")
        if self.test_failed:
            parts.append(f"{self.test_failed} test failures")
        return "⚠️  Pre-existing: " + ", ".join(parts)


class HealthChecker:
    def __init__(self, project: ProjectInfo, timeout_s: int = 120):
        self.project = project
        self.timeout = timeout_s

    def run(self, *, skip_tests: bool = False) -> HealthReport:
        kind = self.project.kind
        report = HealthReport(project_kind=kind)

        if kind == "flutter":
            self._flutter_analyze(report)
            if not skip_tests:
                self._flutter_test(report)
        elif kind == "node":
            self._node_lint(report)
            if not skip_tests:
                self._node_test(report)
        elif kind == "python":
            self._python_lint(report)
            if not skip_tests:
                self._python_test(report)
        elif kind == "go":
            self._go_vet(report)
            if not skip_tests:
                self._go_test(report)
        # Other kinds: skip gracefully

        self._build_baseline_ids(report)
        return report

    # ── Flutter ───────────────────────────────────────────────────────────────

    def _flutter_analyze(self, report: HealthReport):
        r = self._exec(["flutter", "analyze", "--no-fatal-infos"])
        report.raw_logs["analyze"] = (r.stdout + "\n" + r.stderr)[-3000:]
        for line in r.stdout.splitlines():
            lower = line.lower()
            if " error " in lower or " error:" in lower:
                report.analyze_errors += 1
            elif " warning " in lower or " info " in lower:
                report.analyze_warnings += 1

    def _flutter_test(self, report: HealthReport):
        r = self._exec(["flutter", "test", "--no-pub"], timeout=180)
        report.raw_logs["test"] = (r.stdout + "\n" + r.stderr)[-3000:]
        m = re.search(r"(\d+):\s*\+(\d+)\s*(?:-(\d+))?", r.stdout)
        if m:
            report.test_passed = int(m.group(2))
            report.test_failed = int(m.group(3) or 0)

    # ── Node / TS / JS ────────────────────────────────────────────────────────

    def _node_lint(self, report: HealthReport):
        pkg = self.project.root / "package.json"
        if not pkg.exists():
            return
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            return
        if "lint" not in scripts:
            return
        r = self._exec(["npm", "run", "-s", "lint"])
        report.raw_logs["lint"] = (r.stdout + "\n" + r.stderr)[-3000:]
        if r.returncode != 0:
            # Crude: count eslint error lines
            report.analyze_errors = len(re.findall(r"error\s", r.stdout, re.IGNORECASE))

    def _node_test(self, report: HealthReport):
        pkg = self.project.root / "package.json"
        if not pkg.exists():
            return
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            return
        if "test" not in scripts:
            return
        r = self._exec(["npm", "test", "--", "--watchAll=false"], timeout=180)
        report.raw_logs["test"] = (r.stdout + "\n" + r.stderr)[-3000:]
        m_pass = re.search(r"Tests:.*?(\d+)\s+passed", r.stdout)
        m_fail = re.search(r"Tests:.*?(\d+)\s+failed", r.stdout)
        if m_pass: report.test_passed = int(m_pass.group(1))
        if m_fail: report.test_failed = int(m_fail.group(1))

    # ── Python ────────────────────────────────────────────────────────────────

    def _python_lint(self, report: HealthReport):
        # Try ruff first, else pyflakes
        for cmd in (["ruff", "check", "."], ["python3", "-m", "pyflakes", "."]):
            r = self._exec(cmd)
            if r.returncode != -1:  # command found
                report.raw_logs["lint"] = (r.stdout + "\n" + r.stderr)[-3000:]
                report.analyze_errors = len(r.stdout.splitlines())
                return

    def _python_test(self, report: HealthReport):
        r = self._exec(["python3", "-m", "pytest", "-q", "--tb=no"], timeout=180)
        report.raw_logs["test"] = (r.stdout + "\n" + r.stderr)[-3000:]
        m = re.search(r"(\d+)\s+passed", r.stdout)
        if m: report.test_passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", r.stdout)
        if m: report.test_failed = int(m.group(1))

    # ── Go ────────────────────────────────────────────────────────────────────

    def _go_vet(self, report: HealthReport):
        r = self._exec(["go", "vet", "./..."])
        report.raw_logs["vet"] = (r.stdout + "\n" + r.stderr)[-3000:]
        if r.returncode != 0:
            report.analyze_errors += 1

    def _go_test(self, report: HealthReport):
        r = self._exec(["go", "test", "-short", "./..."], timeout=180)
        report.raw_logs["test"] = (r.stdout + "\n" + r.stderr)[-3000:]
        report.test_passed = r.stdout.count("--- PASS:")
        report.test_failed = r.stdout.count("--- FAIL:")

    # ── Shared ────────────────────────────────────────────────────────────────

    def _exec(self, cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                cmd, cwd=self.project.root,
                capture_output=True, text=True,
                timeout=timeout or self.timeout,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="command not found")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, returncode=-2, stdout="", stderr="timeout")

    def _build_baseline_ids(self, report: HealthReport):
        """Produce stable hash-ish IDs for each pre-existing issue so Dev's
        new output can be diffed against baseline."""
        combined = report.raw_logs.get("analyze", "") + report.raw_logs.get("test", "")
        for line in combined.splitlines():
            line = line.strip()
            if not line:
                continue
            if any(tag in line.lower() for tag in ("error", "fail")):
                h = hashlib.md5(line.encode()).hexdigest()[:10]
                report.baseline_issues.add(h)

    # ── Display ───────────────────────────────────────────────────────────────

    @staticmethod
    def print_report(report: HealthReport):
        print(f"\n  {'═'*60}")
        print(f"  🏥 PROJECT HEALTH CHECK — {report.project_kind}")
        print(f"  {'═'*60}")
        print(f"  {report.status_line}")
        if report.analyze_errors or report.analyze_warnings:
            print(f"  Analyze: {report.analyze_errors} errors, {report.analyze_warnings} warnings")
        if report.test_passed or report.test_failed:
            total = report.test_passed + report.test_failed
            pct = report.test_passed / total * 100 if total else 0
            print(f"  Tests:   {report.test_passed}/{total} passed ({pct:.0f}%)")
        if report.baseline_issues:
            print(f"  Baseline tracked: {len(report.baseline_issues)} pre-existing issues")
        print(f"  {'═'*60}")
