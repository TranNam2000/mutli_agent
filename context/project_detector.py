"""
Smart project detection for maintain mode.

Responsibilities:
  - Find project root from any CWD (walk up until signal file found)
  - Detect monorepos (multiple nested pubspec.yaml / package.json)
  - Identify git root + load .gitignore patterns
  - Resolve which subproject the task is about (based on task keywords or CWD)
"""
from __future__ import annotations
import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_SIGNALS = {
    "flutter":  ["pubspec.yaml"],
    "node":     ["package.json"],
    "android":  ["build.gradle", "build.gradle.kts"],
    "ios":      ["Podfile", "*.xcodeproj"],
    "python":   ["pyproject.toml", "setup.py", "requirements.txt"],
    "rust":     ["Cargo.toml"],
    "go":       ["go.mod"],
    "java":     ["pom.xml"],
}

CODE_DIRS = {"lib", "src", "app", "packages", "pkg", "internal"}

# Directories we NEVER scan even if .gitignore is missing
ALWAYS_SKIP = {
    ".git", ".dart_tool", ".idea", ".vscode", ".gradle", ".kotlin",
    "build", "dist", "out", "target", "node_modules", ".next",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv",
    ".flutter-plugins", ".flutter-plugins-dependencies",
    "ios/Pods", "android/.gradle", ".multi_agent",
    ".DS_Store", "coverage",
}


@dataclass
class ProjectInfo:
    root:        Path
    git_root:    Path | None
    kind:        str              # "flutter" | "node" | "python" | ... | "unknown"
    name:        str
    is_monorepo: bool
    subprojects: list["ProjectInfo"] = field(default_factory=list)
    gitignore_patterns: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        tag = f" [monorepo:{len(self.subprojects)} subs]" if self.is_monorepo else ""
        return f"<{self.kind}:{self.name} @ {self.root}{tag}>"

    # ── Gitignore-aware path filter ───────────────────────────────────────────

    def should_skip(self, path: Path) -> bool:
        """Return True if path should be excluded from scans."""
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return True
        parts = rel.parts
        for part in parts:
            if part in ALWAYS_SKIP:
                return True
        rel_str = str(rel)
        for pattern in self.gitignore_patterns:
            if _gitignore_match(pattern, rel_str, path.is_dir()):
                return True
        return False


# ── .gitignore parsing ────────────────────────────────────────────────────────

def _load_gitignore(root: Path) -> list[str]:
    patterns: list[str] = []
    gitignore = root / ".gitignore"
    if gitignore.exists():
        for line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            patterns.append(s)
    # Also include some common global patterns
    return patterns


def _gitignore_match(pattern: str, rel_path: str, is_dir: bool) -> bool:
    """Simplified gitignore matching — good enough for our skip logic."""
    if pattern.startswith("!"):
        return False  # negation not supported — skip pattern
    if pattern.endswith("/"):
        if not is_dir:
            return False
        pattern = pattern.rstrip("/")
    # If pattern has no slash, match any path segment
    if "/" not in pattern:
        return any(fnmatch.fnmatch(seg, pattern) for seg in rel_path.split("/"))
    # Anchored pattern
    if pattern.startswith("/"):
        pattern = pattern.lstrip("/")
    return fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(rel_path, pattern + "/*")


# ── Signal detection ──────────────────────────────────────────────────────────

def _signals_in_dir(d: Path) -> list[str]:
    """Return list of project kinds whose signals exist in d."""
    kinds = []
    for kind, signals in PROJECT_SIGNALS.items():
        for sig in signals:
            if "*" in sig:
                if any(d.glob(sig)):
                    kinds.append(kind)
                    break
            elif (d / sig).exists():
                kinds.append(kind)
                break
    return kinds


def _detect_kind(root: Path) -> str:
    kinds = _signals_in_dir(root)
    if not kinds:
        return "unknown"
    # Flutter takes priority over android/ios since Flutter has both
    priority = ["flutter", "node", "rust", "go", "python", "java", "android", "ios"]
    for p in priority:
        if p in kinds:
            return p
    return kinds[0]


def _extract_name(root: Path, kind: str) -> str:
    """Pull human-readable name from the signal file."""
    try:
        if kind == "flutter":
            for line in (root / "pubspec.yaml").read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"^name:\s*(.+)", line.strip())
                if m: return m.group(1).strip()
        elif kind == "node":
            import json
            data = json.loads((root / "package.json").read_text(encoding="utf-8", errors="ignore"))
            return data.get("name") or root.name
        elif kind == "rust":
            for line in (root / "Cargo.toml").read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r'^name\s*=\s*["\'](.+)["\']', line.strip())
                if m: return m.group(1).strip()
        elif kind == "go":
            for line in (root / "go.mod").read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r"^module\s+(\S+)", line.strip())
                if m: return m.group(1).split("/")[-1]
        elif kind == "python":
            for f in ("pyproject.toml", "setup.py"):
                p = root / f
                if p.exists():
                    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                        m = re.match(r'^name\s*=\s*["\'](.+)["\']', line.strip())
                        if m: return m.group(1).strip()
    except Exception:
        pass
    return root.name


# ── Git root ──────────────────────────────────────────────────────────────────

def _find_git_root(start: Path) -> Path | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except Exception:
        pass
    # Fallback: walk up looking for .git
    p = start.resolve()
    for _ in range(10):
        if (p / ".git").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


# ── Monorepo scan ─────────────────────────────────────────────────────────────

def _scan_subprojects(root: Path, max_depth: int = 3) -> list[ProjectInfo]:
    """Find nested projects below root (up to max_depth)."""
    subs: list[ProjectInfo] = []

    def walk(d: Path, depth: int):
        if depth > max_depth:
            return
        if d.name in ALWAYS_SKIP:
            return
        # Check if this dir itself is a project (but not the root itself)
        if d != root:
            kinds = _signals_in_dir(d)
            if kinds:
                kind = _detect_kind(d)
                subs.append(ProjectInfo(
                    root=d,
                    git_root=_find_git_root(d),
                    kind=kind,
                    name=_extract_name(d, kind),
                    is_monorepo=False,
                    gitignore_patterns=_load_gitignore(d),
                ))
                return  # don't recurse into project — it's self-contained
        # Recurse
        try:
            for child in d.iterdir():
                if child.is_dir():
                    walk(child, depth + 1)
        except PermissionError:
            pass

    walk(root, 0)
    return subs


# ── Main entry points ─────────────────────────────────────────────────────────

def find_project_root(start: Path | str | None = None) -> Path | None:
    """Walk up from `start` (or cwd) to find the nearest project signal."""
    start_path = Path(start).resolve() if start else Path.cwd().resolve()
    here = Path(__file__).resolve().parent.parent  # multi_agent/ itself — never return this

    current = start_path
    for _ in range(8):
        if current == here:
            current = current.parent
            continue
        if _signals_in_dir(current):
            return current
        # Also treat git root as a candidate
        git_root = _find_git_root(current)
        if git_root and git_root != here and _signals_in_dir(git_root):
            return git_root
        if current.parent == current:
            break
        current = current.parent
    return None


def detect_project(start: Path | str | None = None,
                   task_hint: str = "") -> ProjectInfo | None:
    """
    Full detection:
      1. Find project root
      2. Detect kind
      3. Scan for monorepo subprojects
      4. If monorepo + task_hint → pick best subproject based on keywords
    """
    root = find_project_root(start)
    if not root:
        return None

    kind = _detect_kind(root)
    subprojects = _scan_subprojects(root)
    is_monorepo = len(subprojects) >= 2

    info = ProjectInfo(
        root=root,
        git_root=_find_git_root(root),
        kind=kind,
        name=_extract_name(root, kind),
        is_monorepo=is_monorepo,
        subprojects=subprojects,
        gitignore_patterns=_load_gitignore(root),
    )

    # For monorepos with a task hint, try to pick the relevant subproject
    if is_monorepo and task_hint:
        best = _pick_subproject(subprojects, task_hint)
        if best:
            # Replace root with the forsen subproject but keep parent info
            best.is_monorepo = False
            return best

    return info


def _pick_subproject(subs: list[ProjectInfo], task_hint: str) -> ProjectInfo | None:
    """Match task keywords against subproject names/paths."""
    hint_words = [w.lower() for w in re.split(r"\W+", task_hint) if len(w) >= 3]
    best_score = 0
    best: ProjectInfo | None = None
    for sub in subs:
        score = 0
        path_str = str(sub.root).lower() + " " + sub.name.lower()
        for w in hint_words:
            if w in path_str:
                score += 1
        if score > best_score:
            best_score = score
            best = sub
    return best


# ── Name slugification for output folders ────────────────────────────────────

def slugify_name(name: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^\w]", "_", name.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:max_len]
    return slug or "unnamed"
