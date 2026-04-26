"""
Reads an existing codebase and produces a structured project_context.md
that agents can use when working on a maintenance/feature task.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

# Files/dirs to always skip
SKIP_DIRS = {
    ".git", ".dart_tool", ".idea", ".gradle", "build", "node_modules",
    "__pycache__", ".flutter-plugins", ".flutter-plugins-dependencies",
    "ios/Pods", "android/.gradle", "multi_agent",
}
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".ttf", ".otf",
             ".woff", ".woff2", ".lock", ".g.dart", ".freezed.dart"}

# Key config files to read in full
KEY_FILES = [
    "pubspec.yaml", "package.json", "build.gradle", "Podfile",
    "README.md", "ARCHITECTURE.md", "CHANGELOG.md",
    "lib/main.dart", "src/main.ts", "src/index.ts", "src/app.ts",
]

# Source file extensions worth scanning
SOURCE_EXTS = {".dart", ".ts", ".js", ".py", ".go", ".kt", ".swift"}

MAX_FILE_CHARS  = 10000  # max chars per source file
MAX_TOTAL_CHARS = 120000 # total context budget (Claude 200K context — plenty of room)


def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    return path.suffix in SKIP_EXTS or path.name.endswith((".g.dart", ".freezed.dart"))


def _read_file(path: Path, max_chars: int = MAX_FILE_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n... [truncated, {len(text)} chars total]"
        return text
    except (OSError, UnicodeDecodeError):
        return ""


def _folder_tree(root: Path, max_depth: int = 4) -> str:
    lines = []
    def walk(path: Path, depth: int, prefix: str):
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for i, entry in enumerate(entries):
            if _should_skip(entry):
                continue
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                walk(entry, depth + 1, prefix + extension)
    lines.append(root.name + "/")
    walk(root, 1, "")
    return "\n".join(lines)


def _detect_stack(root: Path) -> str:
    indicators = []
    if (root / "pubspec.yaml").exists():
        indicators.append("Flutter/Dart")
    if (root / "package.json").exists():
        pkg = _read_file(root / "package.json", 2000)
        if "react" in pkg:      indicators.append("React")
        if "next" in pkg:       indicators.append("Next.js")
        if "express" in pkg:    indicators.append("Express.js")
        if "fastify" in pkg:    indicators.append("Fastify")
        if "typescript" in pkg: indicators.append("TypeScript")
        else:                   indicators.append("Node.js")
    if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
        indicators.append("Python")
    if (root / "go.mod").exists():
        indicators.append("Go")
    return ", ".join(indicators) if indicators else "Unknown"


def _priority_score(path: Path, task_keywords: list[str]) -> int:
    """Higher = read first. Design system + task-relevant files get priority."""
    name  = path.name.lower()
    parts = [p.lower() for p in path.parts]
    score = 0

    # Design system / theme files — always high priority
    if any(k in name for k in ("color", "theme", "style", "dimension", "constant", "typography", "app_")):
        score += 40
    # Routing / DI / main entry points
    if any(k in name for k in ("route", "router", "injection", "main", "app.dart")):
        score += 30
    # Base / shared widgets
    if any(k in parts for k in ("core", "shared", "common", "base", "utils")):
        score += 20
    # Task keyword match in filename or path
    for kw in task_keywords:
        if kw in name or any(kw in p for p in parts):
            score += 50
    # Shallow files first (fewer path parts = closer to root)
    score -= len(path.parts) * 2
    return score


def read_project(project_dir: str | Path, task_hint: str = "") -> str:
    """
    Scan project_dir and return a structured markdown summary.
    task_hint: keywords from the task (e.g. "home screen") to prioritize relevant files.
    """
    root = Path(project_dir).resolve()
    if not root.exists():
        raise ValueError(f"Project dir không tồn tại: {root}")

    # Extract keywords from task hint for smart prioritization
    task_keywords = [w.lower() for w in re.split(r"\W+", task_hint) if len(w) >= 4]

    sections: list[str] = []
    budget = MAX_TOTAL_CHARS

    # ── 1. Overview ──────────────────────────────────────────────────────────
    stack = _detect_stack(root)
    sections.append(f"# Project Context: {root.name}\n")
    sections.append(f"**Path:** `{root}`  \n**Stack:** {stack}\n")

    # ── 2. Key config files — read fully ─────────────────────────────────────
    sections.append("\n## Key Configuration Files\n")
    for fname in KEY_FILES:
        fpath = root / fname
        if fpath.exists():
            content = _read_file(fpath, min(5000, budget))
            budget -= len(content)
            sections.append(f"### {fname}\n```\n{content}\n```\n")
            if budget <= 500:
                break

    # ── 3. Folder structure ───────────────────────────────────────────────────
    sections.append("\n## Folder Structure\n```\n")
    tree = _folder_tree(root)
    sections.append(tree + "\n```\n")
    budget -= len(tree)

    # ── 4. Source files — sorted by priority ─────────────────────────────────
    if budget > 2000:
        sections.append("\n## Source Files\n")
        source_files = [
            p for p in root.rglob("*")
            if p.is_file() and p.suffix in SOURCE_EXTS and not _should_skip(p)
        ]
        # Sort by priority descending
        source_files.sort(key=lambda p: _priority_score(p, task_keywords), reverse=True)

        for fpath in source_files[:60]:  # up to 60 files
            if budget <= 500:
                break
            rel     = fpath.relative_to(root)
            per_file_limit = min(MAX_FILE_CHARS, budget)
            content = _read_file(fpath, per_file_limit)
            budget -= len(content)
            sections.append(f"### {rel}\n```{fpath.suffix.lstrip('.')}\n{content}\n```\n")

    return "".join(sections)


def detect_project_name(project_dir: str | Path) -> str:
    """
    Auto-detect project name from config files.
    Priority: pubspec.yaml → package.json → go.mod → pyproject.toml → Cargo.toml → dir name.
    Returns a filesystem-safe slug (lowercase, underscores, max 40 chars).
    """
    root = Path(project_dir).resolve()

    name = ""

    pubspec = root / "pubspec.yaml"
    if not name and pubspec.exists():
        for line in pubspec.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^name:\s*(.+)", line.strip())
            if m:
                name = m.group(1).strip()
                break

    pkg = root / "package.json"
    if not name and pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            name = data.get("name", "")
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    gomod = root / "go.mod"
    if not name and gomod.exists():
        for line in gomod.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^module\s+(\S+)", line.strip())
            if m:
                name = m.group(1).split("/")[-1]
                break

    for toml_file in [root / "pyproject.toml", root / "Cargo.toml"]:
        if not name and toml_file.exists():
            for line in toml_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = re.match(r'^name\s*=\s*["\'](.+)["\']', line.strip())
                if m:
                    name = m.group(1).strip()
                    break

    name = name or root.name

    # Sanitize: lowercase, replace non-alphanumeric with underscore, max 40 chars
    slug = re.sub(r"[^\w]", "_", name.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:40]
    return slug or "unnamed"


def save_context(project_dir: str | Path, output_path: str | Path, task_hint: str = "") -> str:
    """Read project and save context to output_path. Returns the context text."""
    context = read_project(project_dir, task_hint=task_hint)
    Path(output_path).write_text(context, encoding="utf-8")
    return context
