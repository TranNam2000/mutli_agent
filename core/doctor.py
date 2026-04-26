"""
Doctor — check if all external CLIs & dependencies are installed.

Run:  python main.py --doctor  (or `mag --doctor`)
"""
from __future__ import annotations
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class CheckResult:
    name:     str
    ok:       bool
    version:  str = ""
    hint:     str = ""
    critical: bool = True  # if False, pipeline works without it


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""


def _check_cmd(name: str, cmd: list[str], install_hint: str,
               critical: bool = True) -> CheckResult:
    if not shutil.which(cmd[0]):
        return CheckResult(name, False, hint=install_hint, critical=critical)
    code, out = _run(cmd)
    if code != 0:
        return CheckResult(name, False, hint=install_hint, critical=critical)
    # Extract version-ish string (first line, first 80 chars)
    version = out.splitlines()[0][:80] if out else "(installed)"
    return CheckResult(name, True, version=version, critical=critical)


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    # Core runtime
    results.append(CheckResult(
        "Python", sys.version_info >= (3, 10),
        version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        hint="Python ≥ 3.10 required",
        critical=True,
    ))

    # Claude CLI — absolutely required
    results.append(_check_cmd(
        "claude CLI", ["claude", "--version"],
        "npm install -g @anthropic-ai/claude-code (+ `claude login`)",
        critical=True,
    ))

    # Git — required for maintain mode + auto-commit
    results.append(_check_cmd(
        "git", ["git", "--version"],
        "apt install git / brew install git",
        critical=False,
    ))

    # Python deps
    for pkg, hint in [
        ("anthropic", "pip install anthropic>=0.40.0"),
        ("PIL", "pip install Pillow>=10.0.0"),
    ]:
        try:
            __import__(pkg)
            results.append(CheckResult(f"python:{pkg}", True, version="(imported)",
                                        critical=(pkg == "anthropic")))
        except ImportError:
            results.append(CheckResult(f"python:{pkg}", False, hint=hint,
                                        critical=(pkg == "anthropic")))

    # Flutter (only required for Flutter projects)
    results.append(_check_cmd(
        "flutter", ["flutter", "--version"],
        "https://docs.flutter.dev/get-started/install",
        critical=False,
    ))

    # Patrol CLI (for in-app testing)
    results.append(_check_cmd(
        "patrol CLI", ["patrol", "--version"],
        "dart pub global activate patrol_cli",
        critical=False,
    ))

    # Maestro CLI (for E2E)
    results.append(_check_cmd(
        "maestro CLI", ["maestro", "--version"],
        'curl -Ls "https://get.maestro.mobile.dev" | bash',
        critical=False,
    ))

    # ADB (for Android emulator + logcat)
    results.append(_check_cmd(
        "adb", ["adb", "--version"],
        "Android SDK platform-tools (auto-installed with Android Studio)",
        critical=False,
    ))

    # Playwright (only for Stitch UI generator)
    try:
        results.append(CheckResult("python:playwright", True, version="(imported)",
                                    critical=False))
    except ImportError:
        results.append(CheckResult(
            "python:playwright", False,
            hint="pip install playwright && playwright install chromium",
            critical=False,
        ))

    # ripgrep (fast grep for scoped reader) — optional but useful
    results.append(_check_cmd(
        "ripgrep (rg)", ["rg", "--version"],
        "brew install ripgrep / apt install ripgrep",
        critical=False,
    ))

    return results


def print_report(results: list[CheckResult]) -> bool:
    """Print a summary table. Returns True if all CRITICAL checks pass."""
    print("\n" + "═" * 66)
    print("  🏥 MULTI-AGENT DOCTOR — environment check")
    print("═" * 66)
    all_critical_ok = True

    for r in results:
        if not r.ok and r.critical:
            all_critical_ok = False

    groups = [
        ("Critical",  [r for r in results if r.critical]),
        ("Optional",  [r for r in results if not r.critical]),
    ]
    for title, items in groups:
        if not items:
            continue
        print(f"\n  {title}:")
        for r in items:
            if r.ok:
                ver = f" ({r.version})" if r.version and r.version != "(installed)" else ""
                print(f"    ✅ {r.name:<20}{ver}")
            else:
                icon = "❌" if r.critical else "⚠️ "
                print(f"    {icon} {r.name:<20} missing")
                if r.hint:
                    print(f"       → {r.hint}")

    print("\n" + "─" * 66)
    if all_critical_ok:
        missing_optional = sum(1 for r in results if not r.ok and not r.critical)
        if missing_optional:
            print(f"  ✅ Core OK ({missing_optional} optional tools missing — still usable)")
        else:
            print(f"  ✅ All systems GO")
    else:
        print(f"  ❌ Critical dependencies missing — pipeline will fail")
    print("═" * 66)
    return all_critical_ok


def main():
    ok = print_report(run_checks())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
