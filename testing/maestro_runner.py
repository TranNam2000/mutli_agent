"""
Maestro runner — black-box E2E test with screenshot diff.

Maestro tests are YAML flows that run against the built app. They DON'T
require app code changes. Ideal for:
  - E2E user journeys
  - Regression on released builds
  - Screenshot-diff feedback loop (compare against design mocks)

Requires `maestro` CLI installed:
  curl -Ls "https://get.maestro.mobile.dev" | bash

Screenshot diff uses Pillow (pip install Pillow).
"""
from __future__ import annotations
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.logging import tprint


@dataclass
class MaestroFlowResult:
    flow_name:  str
    passed:     bool
    duration_s: float
    steps_total: int
    steps_failed: int
    screenshots: list[str] = field(default_factory=list)
    error_log:  str = ""


@dataclass
class MaestroSuiteResult:
    flows: list[MaestroFlowResult]
    total_duration_s: float

    @property
    def all_passed(self) -> bool:
        return bool(self.flows) and all(f.passed for f in self.flows)

    @property
    def pass_count(self) -> int:
        return sum(1 for f in self.flows if f.passed)


class MaestroRunner:
    MAESTRO_CLI_CHECK = ["maestro", "--version"]

    def __init__(self, project_dir: str | Path, flows_dir: str = "maestro"):
        self.project_dir = Path(project_dir).resolve()
        self.flows_dir   = self.project_dir / flows_dir
        self.screenshot_dir = self.project_dir / "build" / "maestro_screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def ensure_installed(self) -> bool:
        try:
            r = subprocess.run(self.MAESTRO_CLI_CHECK, capture_output=True,
                               text=True, timeout=10)
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            tprint(
                '  ⚠️  Maestro CLI not yet cài. Cài bằng:\n'
                '     curl -Ls "https://get.maestro.mobile.dev" | bash\n'
                '     Thêm ~/.maestro/bin into PATH'
            )
            return False

    # ── Flow installation ─────────────────────────────────────────────────────

    def install_flows(self, flows: dict[str, str]):
        """Write flows dict {name.yaml: yaml_content} to maestro/ dir."""
        self.flows_dir.mkdir(parents=True, exist_ok=True)
        for fname, content in flows.items():
            if not fname.endswith((".yaml", ".yml")):
                fname += ".yaml"
            (self.flows_dir / fname).write_text(content, encoding="utf-8")

    def list_flows(self) -> list[Path]:
        if not self.flows_dir.exists():
            return []
        return sorted(self.flows_dir.rglob("*.yaml")) + sorted(self.flows_dir.rglob("*.yml"))

    # ── Run ───────────────────────────────────────────────────────────────────

    def run_flow(self, flow_path: Path) -> MaestroFlowResult:
        cmd = ["maestro", "test", str(flow_path),
               "--format", "junit",
               "--output", str(self.screenshot_dir / f"{flow_path.stem}.xml")]

        env = {**os.environ, "MAESTRO_SCREENSHOT_DIR": str(self.screenshot_dir)}

        try:
            r = subprocess.run(cmd, cwd=self.project_dir, env=env,
                               capture_output=True, text=True, timeout=600)
            return self._parse_flow(flow_path, r.stdout + "\n" + r.stderr, r.returncode)
        except subprocess.TimeoutExpired:
            return MaestroFlowResult(flow_path.stem, False, 600.0, 0, 1,
                                     [], "TIMEOUT")

    def run_all(self, tag_filter: str | None = None) -> MaestroSuiteResult:
        """Run all flows in flows_dir. Optionally filter by tag."""
        if not self.ensure_installed():
            return MaestroSuiteResult([], 0.0)
        flows = self.list_flows()
        if tag_filter:
            flows = [f for f in flows if tag_filter in f.read_text(encoding="utf-8")]
        results: list[MaestroFlowResult] = []
        total = 0.0
        for f in flows:
            tprint(f"  ▶  Maestro flow: {f.name}")
            res = self.run_flow(f)
            total += res.duration_s
            results.append(res)
            icon = "✅" if res.passed else "❌"
            tprint(f"     {icon} {res.steps_total - res.steps_failed}/{res.steps_total} steps in {res.duration_s:.1f}s")
        return MaestroSuiteResult(flows=results, total_duration_s=total)

    def _parse_flow(self, path: Path, output: str, returncode: int) -> MaestroFlowResult:
        # Extract step count from output
        steps = re.findall(r"\u2713|\u2717|PASS|FAIL", output)
        failed = len(re.findall(r"\u2717|FAIL", output))
        m_dur = re.search(r"(\d+[.,]\d+)\s*s(?:econds?)?", output)
        duration = float(m_dur.group(1).replace(",", ".")) if m_dur else 0.0

        # Collect screenshots for this flow
        shots = sorted(self.screenshot_dir.glob(f"{path.stem}*.png"))
        shot_paths = [str(s) for s in shots]

        return MaestroFlowResult(
            flow_name=path.stem,
            passed=(returncode == 0),
            duration_s=duration,
            steps_total=max(len(steps), 1),
            steps_failed=failed,
            screenshots=shot_paths,
            error_log=output[-2000:] if returncode != 0 else "",
        )

    # ── Screenshot diff ───────────────────────────────────────────────────────

    def compare_screenshot(self, actual_path: str, baseline_path: str,
                           tolerance: float = 0.03) -> dict:
        """
        Compare two screenshots. Returns {match, diff_pct, diff_image_path}.
        tolerance: max allowed pixel diff ratio (0.03 = 3%).
        Requires Pillow.
        """
        try:
            from PIL import Image, ImageForps
        except ImportError:
            return {"match": False, "diff_pct": 1.0, "error": "Pillow not installed"}

        a = Image.open(actual_path).convert("RGB")
        b = Image.open(baseline_path).convert("RGB")
        if a.size != b.size:
            b = b.resize(a.size)
        diff = ImageForps.difference(a, b)
        bbox = diff.getbbox()
        if not bbox:
            return {"match": True, "diff_pct": 0.0}

        # Compute pixel diff percentage
        pixels_a = list(a.getdata())
        pixels_b = list(b.getdata())
        diff_count = sum(
            1 for pa, pb in zip(pixels_a, pixels_b)
            if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) > 30
        )
        pct = diff_count / len(pixels_a)

        diff_img_path = str(Path(actual_path).with_suffix(".diff.png"))
        diff.save(diff_img_path)

        return {
            "match":    pct <= tolerance,
            "diff_pct": pct,
            "diff_image_path": diff_img_path,
        }

    def compare_to_design(self, actual_screenshot: str, design_specs: str,
                          claude_call_with_image) -> dict:
        """
        When no pixel baseline exists, use Claude vision to compare actual screenshot
        against textual design specs. Returns {aligned, issues: [...]}.
        """
        system = (
            "You review UI. So sánh screenshot thực tế with design specs. "
            "Chỉ liệt kê issue nhìn thấy rõ ràng in ảnh."
        )
        prompt = (
            f"Design Specs:\n{design_specs[:2000]}\n\n"
            "With screenshot này:\n"
            "ALIGNED: YES|NO\n"
            "ISSUES:\n"
            "- [vấn đề cụ thể with bằng chứng]"
        )
        try:
            raw = claude_call_with_image(system, prompt, actual_screenshot)
        except Exception as e:
            return {"aligned": False, "issues": [f"vision call failed: {e}"]}
        aligned = "ALIGNED: YES" in raw
        issues = [
            line.lstrip("- ").strip()
            for line in raw.splitlines()
            if line.strip().startswith("- ")
        ]
        return {"aligned": aligned, "issues": issues[:10], "raw": raw}

    # ── Report ────────────────────────────────────────────────────────────────

    def format_suite_report(self, sr: MaestroSuiteResult) -> str:
        lines = [f"\n  {'═'*60}",
                 f"  🌊 MAESTRO E2E RESULTS",
                 f"  {'═'*60}"]
        for f in sr.flows:
            icon = "✅" if f.passed else "❌"
            lines.append(f"  {icon} {f.flow_name:40}  {f.steps_total - f.steps_failed}/{f.steps_total}  ({f.duration_s:.1f}s)")
        overall = f"✅ {sr.pass_count}/{len(sr.flows)} PASSED" if sr.all_passed else f"❌ {sr.pass_count}/{len(sr.flows)} PASSED"
        lines += [f"  {'─'*60}", f"  {overall}  (total {sr.total_duration_s:.1f}s)", f"  {'═'*60}"]
        return "\n".join(lines)
