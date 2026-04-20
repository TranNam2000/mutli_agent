"""
Patrol test runner — replaces flutter_runner.py.

Patrol is an extension of flutter_test that handles native dialogs,
biometrics, notifications, and runs on real devices/emulators without
code instrumentation changes to the app.

Usage:
  runner = PatrolRunner(project_dir)
  result = runner.run_tests(code, platform='android')
"""
from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PatrolResult:
    platform:   str
    device_id:  str
    device_name: str
    passed:     int
    failed:     int
    skipped:    int
    duration_s: float
    failures:   list[str]
    raw_output: str
    success:    bool
    screenshot_paths: list[str]


@dataclass
class PatrolMultiResult:
    android: PatrolResult | None
    ios:     PatrolResult | None

    @property
    def all_passed(self) -> bool:
        results = [r for r in [self.android, self.ios] if r is not None]
        return bool(results) and all(r.success for r in results)

    def all_failures(self) -> list[str]:
        out = []
        for r in [self.android, self.ios]:
            if r and not r.success:
                out += [f"[{r.platform}] {f}" for f in r.failures]
        return out

    def all_screenshots(self) -> list[str]:
        out = []
        for r in [self.android, self.ios]:
            if r:
                out.extend(r.screenshot_paths)
        return out


class PatrolRunner:
    INTEGRATION_TEST_DIR = "integration_test"
    PATROL_CLI_CHECK = ["patrol", "--version"]

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    # ── Setup check ───────────────────────────────────────────────────────────

    def ensure_patrol_installed(self) -> bool:
        """Check if patrol CLI is available; if not, print install instruction."""
        try:
            r = subprocess.run(self.PATROL_CLI_CHECK, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        print(
            "  ⚠️  Patrol CLI not yet cài. Cài bằng:\n"
            "     dart pub global activate patrol_cli\n"
            "     Thêm into pubspec.yaml dev_dependencies: patrol: ^3.0.0"
        )
        return False

    def ensure_patrol_dep(self) -> bool:
        """Make sure pubspec.yaml has patrol dev dependency."""
        pubspec = self.project_dir / "pubspec.yaml"
        if not pubspec.exists():
            return False
        content = pubspec.read_text(encoding="utf-8")
        if "patrol:" in content:
            return True
        # Auto-add to dev_dependencies
        if "dev_dependencies:" in content:
            new = re.sub(
                r"(dev_dependencies:\s*\n)",
                "\\1  patrol: ^3.0.0\n",
                content,
                count=1,
            )
            pubspec.write_text(new, encoding="utf-8")
            print("  ➕ Done thêm patrol into pubspec.yaml dev_dependencies")
            subprocess.run(["flutter", "pub", "get"], cwd=self.project_dir, capture_output=True)
            return True
        return False

    # ── Device discovery ──────────────────────────────────────────────────────

    def list_devices(self) -> list[dict]:
        try:
            r = subprocess.run(
                ["flutter", "devices", "--machine"],
                cwd=self.project_dir,
                capture_output=True, text=True, timeout=30,
            )
            return json.loads(r.stdout) if r.stdout.strip() else []
        except Exception:
            return []

    def find_device(self, platform: str) -> dict | None:
        devices = self.list_devices()
        p = platform.lower()
        matches = [d for d in devices
                   if p in d.get("targetPlatform", "").lower()
                   or p in d.get("name", "").lower()]
        if not matches:
            return None
        # Prefer emulator/simulator
        emu = [d for d in matches if d.get("isDevice") is False
               or "emulator" in d.get("name", "").lower()
               or "simulator" in d.get("name", "").lower()]
        return emu[0] if emu else matches[0]

    # ── Install test code ─────────────────────────────────────────────────────

    def install_test_file(self, code: str, filename: str = "patrol_test.dart") -> Path:
        test_dir = self.project_dir / self.INTEGRATION_TEST_DIR
        test_dir.mkdir(parents=True, exist_ok=True)
        path = test_dir / filename
        path.write_text(code, encoding="utf-8")
        return path

    # ── Run ───────────────────────────────────────────────────────────────────

    def run_tests(self, code: str, platform: str = "android",
                  tags: str | None = None) -> PatrolResult | None:
        """Run patrol tests on specified platform."""
        if not self.ensure_patrol_installed():
            return None
        self.ensure_patrol_dep()

        device = self.find_device(platform)
        if not device:
            print(f"  ❌ No có {platform} device — skip.")
            return None

        test_path = self.install_test_file(code)
        device_id = device.get("id", "")
        device_name = device.get("name", device_id)

        print(f"  📱 [{platform.upper()}] Patrol chạy trên: {device_name}")

        cmd = [
            "patrol", "test",
            "--target", str(test_path.relative_to(self.project_dir)),
            "--device", device_id,
        ]
        if tags:
            cmd += ["--tag", tags]

        try:
            r = subprocess.run(
                cmd, cwd=self.project_dir,
                capture_output=True, text=True, timeout=1200,
            )
            return self._parse(r.stdout + "\n" + r.stderr,
                               r.returncode, platform, device_id, device_name)
        except subprocess.TimeoutExpired:
            return PatrolResult(platform, device_id, device_name, 0, 0, 0,
                                1200.0, ["TIMEOUT"], "", False, [])

    def run_all_platforms(self, code: str) -> PatrolMultiResult:
        android = self.run_tests(code, "android")
        ios     = self.run_tests(code, "ios")
        return PatrolMultiResult(android=android, ios=ios)

    # ── Parse ─────────────────────────────────────────────────────────────────

    def _parse(self, output: str, returncode: int,
               platform: str, device_id: str, device_name: str) -> PatrolResult:
        passed  = len(re.findall(r"\u2713|\+\s|PASS(?:ED)?", output))
        failed_matches = re.findall(r"\u2717\s+(.+)|FAIL(?:ED)?\s+(.+)|- FAILED:\s*(.+)", output)
        failures = []
        for m in failed_matches:
            # each is a tuple with groups; join non-empty
            for g in (m if isinstance(m, tuple) else (m,)):
                if g:
                    failures.append(g.strip())
                    break

        skipped = len(re.findall(r"SKIPPED|- SKIP", output))
        m_dur   = re.search(r"(\d+[.,]\d+)\s*s(?:econds?)?", output)
        duration = float(m_dur.group(1).replace(",", ".")) if m_dur else 0.0

        # Screenshots collected by patrol go to build/patrol_*
        shot_dir = self.project_dir / "build" / "patrol_screenshots"
        screenshots = [str(p) for p in shot_dir.rglob("*.png")] if shot_dir.exists() else []

        return PatrolResult(
            platform=platform, device_id=device_id, device_name=device_name,
            passed=passed, failed=len(failures), skipped=skipped,
            duration_s=duration, failures=failures[:10],
            raw_output=output[-4000:], success=(returncode == 0),
            screenshot_paths=screenshots,
        )

    # ── Report ────────────────────────────────────────────────────────────────

    def format_report(self, r: PatrolResult) -> str:
        icon = "✅" if r.success else "❌"
        lines = [
            f"  {icon} [{r.platform.upper()}] {r.device_name}",
            f"     Passed: {r.passed}  Failed: {r.failed}  Skipped: {r.skipped}  ({r.duration_s:.1f}s)",
        ]
        for f in r.failures[:5]:
            lines.append(f"     ✗ {f}")
        if r.screenshot_paths:
            lines.append(f"     📸 {len(r.screenshot_paths)} screenshots in build/patrol_screenshots/")
        return "\n".join(lines)

    def format_multi_report(self, mr: PatrolMultiResult) -> str:
        lines = [f"\n  {'═'*60}",
                 f"  🧪 PATROL TEST RESULTS — MULTI-PLATFORM",
                 f"  {'═'*60}"]
        for r in [mr.android, mr.ios]:
            if r:
                lines.append(self.format_report(r))
        overall = "✅ ALL PLATFORMS PASSED" if mr.all_passed else "❌ SOME PLATFORMS FAILED"
        lines += [f"  {'─'*60}", f"  {overall}", f"  {'═'*60}"]
        return "\n".join(lines)
