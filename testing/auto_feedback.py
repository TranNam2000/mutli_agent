"""
Auto-feedback loop.

After pipeline completes, automatically:
  1. Build the app (apk / ios build)
  2. Run Maestro flows on emulator
  3. Capture screenshots + device logs
  4. Diff screenshots against design specs (via Claude vision)
  5. Scrape logcat for crashes / errors
  6. Generate a structured feedback report
  7. Feed back to BA → orchestrator.run_feedback() for auto self-healing

This closes the loop: pipeline → built app → real issues → re-run affected steps.
"""
from __future__ import annotations
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FeedbackItem:
    type:        str  # Bug | UX Issue | Crash | Performance
    severity:    str  # BLOCKER | CRITICAL | MAJOR | MINOR
    description: str
    evidence:    str = ""          # log line / screenshot path
    screenshot:  str | None = None


@dataclass
class FeedbackReport:
    items:     list[FeedbackItem] = field(default_factory=list)
    pass_rate: float = 0.0
    screenshots: list[str] = field(default_factory=list)
    build_succeeded: bool = True

    @property
    def has_blockers(self) -> bool:
        return any(i.severity == "BLOCKER" for i in self.items)

    def to_feedback_dict(self) -> dict:
        """Convert to the structure orchestrator.run_feedback() expects."""
        if not self.items:
            return {}
        top = self.items[0]
        bundled_desc = "\n".join(
            f"[{i.severity}] {i.type}: {i.description}"
            + (f"\n  Evidence: {i.evidence[:200]}" if i.evidence else "")
            for i in self.items[:8]
        )
        return {
            "type": top.type,
            "description": bundled_desc,
            "screenshot_path": top.screenshot or "",
            "auto_generated": True,
            "items": [
                {"type": i.type, "severity": i.severity,
                 "description": i.description, "evidence": i.evidence}
                for i in self.items
            ],
        }


class AutoFeedback:
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.logcat_dir  = self.project_dir / "build" / "logcat"
        self.logcat_dir.mkdir(parents=True, exist_ok=True)

    # ── Build ─────────────────────────────────────────────────────────────────

    def build_debug(self, platform: str = "android") -> bool:
        """Build debug APK/IPA. Returns True if build succeeded."""
        if platform == "android":
            cmd = ["flutter", "build", "apk", "--debug"]
        elif platform == "ios":
            cmd = ["flutter", "build", "ios", "--debug", "--no-codesign"]
        else:
            return False

        print(f"  🔨 Building {platform} debug...")
        try:
            r = subprocess.run(cmd, cwd=self.project_dir,
                               capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                print(f"  ❌ Build FAIL:\n{r.stderr[-1000:]}")
                return False
            print(f"  ✅ Build OK")
            return True
        except subprocess.TimeoutExpired:
            print("  ❌ Build timeout (10min)")
            return False

    # ── Logcat scraper ────────────────────────────────────────────────────────

    def capture_logcat(self, duration_s: int = 60) -> Path:
        """Capture adb logcat while Maestro runs. Returns log file path."""
        log_file = self.logcat_dir / "runtime.log"
        try:
            # Clear first
            subprocess.run(["adb", "logcat", "-c"], capture_output=True, timeout=5)
            with open(log_file, "w") as f:
                proc = subprocess.Popen(
                    ["adb", "logcat", "-v", "time"],
                    stdout=f, stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=duration_s)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return log_file

    def scrape_crashes(self, log_file: Path) -> list[FeedbackItem]:
        """Extract crash / fatal / ANR entries from logcat."""
        if not log_file.exists():
            return []

        items: list[FeedbackItem] = []
        content = log_file.read_text(errors="ignore")

        # Android patterns
        crash_patterns = [
            (r"FATAL EXCEPTION:.*?(?=\n\w|\Z)", "Crash", "BLOCKER"),
            (r"ANR in .+?(?=\n)", "Performance", "CRITICAL"),
            (r"E/flutter.*?Exception:.+?(?=\n)", "Bug", "CRITICAL"),
            (r"E/AndroidRuntime.*?(?=\n)", "Bug", "CRITICAL"),
            (r"OutOfMemoryError.+?(?=\n)", "Performance", "BLOCKER"),
        ]
        for pat, tp, sev in crash_patterns:
            for m in re.finditer(pat, content, re.MULTILINE | re.DOTALL):
                snippet = m.group(0)[:300].strip()
                items.append(FeedbackItem(
                    type=tp, severity=sev,
                    description=snippet.splitlines()[0][:150],
                    evidence=snippet,
                ))
                if len(items) >= 15:
                    break
        return items

    # ── Maestro integration ──────────────────────────────────────────────────

    def run_maestro_and_diff(self, design_specs: str,
                             claude_call_with_image,
                             flows_dir: str = "maestro") -> list[FeedbackItem]:
        """Run Maestro flows, diff each screenshot against design specs."""
        from testing.maestro_runner import MaestroRunner

        runner = MaestroRunner(self.project_dir, flows_dir)
        if not runner.ensure_installed():
            return [FeedbackItem(
                type="Other", severity="MINOR",
                description="Maestro CLI not yet cài — skip E2E check",
            )]

        suite = runner.run_all()
        print(runner.format_suite_report(suite))

        items: list[FeedbackItem] = []

        # Failed flows → blocker feedback
        for f in suite.flows:
            if not f.passed:
                items.append(FeedbackItem(
                    type="Bug", severity="BLOCKER",
                    description=f"Maestro flow '{f.flow_name}' failed",
                    evidence=f.error_log[-400:],
                    screenshot=f.screenshots[0] if f.screenshots else None,
                ))

        # Visual diff on screenshots
        all_shots = [s for f in suite.flows for s in f.screenshots]
        for shot in all_shots[:8]:  # cap at 8 to limit vision API cost
            diff = runner.compare_to_design(shot, design_specs, claude_call_with_image)
            if not diff.get("aligned"):
                for issue in diff.get("issues", [])[:3]:
                    items.append(FeedbackItem(
                        type="UX Issue", severity="MAJOR",
                        description=issue, evidence=shot, screenshot=shot,
                    ))

        return items

    # ── Main orchestration ───────────────────────────────────────────────────

    def collect(self, design_specs: str, claude_call_with_image,
                platform: str = "android") -> FeedbackReport:
        """End-to-end feedback collection."""
        report = FeedbackReport()

        # 1. Build
        if not self.build_debug(platform):
            report.build_succeeded = False
            report.items.append(FeedbackItem(
                type="Bug", severity="BLOCKER",
                description=f"{platform} build failed — fix compile errors first",
            ))
            return report

        # 2. Logcat in background while Maestro runs
        log_file = None
        try:
            import threading
            log_thread = threading.Thread(
                target=lambda: self.capture_logcat(duration_s=180)
            )
            log_thread.daemon = True
            log_thread.start()
        except Exception:
            pass

        # 3. Maestro + visual diff
        maestro_items = self.run_maestro_and_diff(design_specs, claude_call_with_image)
        report.items.extend(maestro_items)

        # 4. Scrape crashes from logcat (after Maestro done)
        log_file = self.logcat_dir / "runtime.log"
        crash_items = self.scrape_crashes(log_file)
        report.items.extend(crash_items)

        # 5. Compute pass rate summary
        total_checks = max(len(report.items) + 5, 1)  # +5 = assumed base checks passed
        failed = len(report.items)
        report.pass_rate = max(0.0, 1.0 - failed / total_checks)

        return report

    # ── Display ───────────────────────────────────────────────────────────────

    def print_report(self, report: FeedbackReport):
        print(f"\n  {'═'*60}")
        print(f"  💬 AUTO-FEEDBACK REPORT")
        print(f"  {'═'*60}")
        print(f"  Build           : {'✅ OK' if report.build_succeeded else '❌ FAILED'}")
        print(f"  Issues found    : {len(report.items)}")
        if report.items:
            by_sev: dict[str, int] = {}
            for i in report.items:
                by_sev[i.severity] = by_sev.get(i.severity, 0) + 1
            print(f"  By severity     : " + ", ".join(f"{k}:{v}" for k, v in by_sev.items()))

        print(f"  Pass rate est.  : {report.pass_rate*100:.1f}%")
        print(f"  {'─'*60}")
        for i in report.items[:10]:
            sev_icon = {"BLOCKER": "🔴", "CRITICAL": "🟠", "MAJOR": "🟡", "MINOR": "🔵"}.get(i.severity, "⚪")
            print(f"  {sev_icon} [{i.type}] {i.description[:90]}")
            if i.evidence:
                print(f"      ↳ {i.evidence[:120].splitlines()[0] if i.evidence else ''}")
        if len(report.items) > 10:
            print(f"  ... +{len(report.items) - 10} more")
        print(f"  {'═'*60}")
