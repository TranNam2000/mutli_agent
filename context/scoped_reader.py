"""
Task-scoped project reader — read only files relevant to the task.

Strategy (cheaper than reading 60 random files):
  1. Extract meaningful keywords from task description
  2. grep -l the codebase for those keywords → anchor files
  3. For each anchor file, resolve its direct imports (max 1 level)
  4. Always include well-known "map" files: main.dart, app.dart, routes, DI container, theme
  5. Budget char count across files; truncate each file to its interesting regions

Falls back to the old project_context_reader.read_project() when no keywords
produce hits.
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

from .project_detector import ProjectInfo


MAX_TOTAL_CHARS = 100_000   # total budget across all included files
PER_FILE_MAX    = 12_000    # per-file cap (truncate longer files)
PER_FILE_MIN    = 600       # read at least this many chars from anchors
MAX_ANCHORS     = 12        # cap anchor files (grep hits)
MAX_IMPORTS     = 20        # extra imports pulled in
STOPWORDS = {
    "how", "what", "when", "where", "why", "with", "this", "that", "these",
    "those", "make", "need", "want", "thêm", "fix", "fix", "add", "new",
    "create", "build", "make", "update", "feature", "tính", "năng", "chức",
    "should", "could", "would", "user", "users", "data", "system",
}

# Map files — always include if exist (helps agents orient)
MAP_FILES = {
    "flutter": [
        "lib/main.dart", "lib/app.dart",
        "lib/app/app_router.dart", "lib/routes.dart", "lib/core/routes.dart",
        "lib/injection_container.dart", "lib/core/di/injection.dart",
        "lib/core/theme/theme.dart", "lib/core/theme.dart",
    ],
    "node": [
        "src/index.ts", "src/app.ts", "src/server.ts", "src/main.ts",
        "src/routes.ts", "src/routes/index.ts", "src/config.ts",
    ],
    "python": ["src/main.py", "app/main.py", "main.py", "src/app.py"],
    "go":     ["main.go", "cmd/main.go"],
    "rust":   ["src/main.rs", "src/lib.rs"],
}

SOURCE_EXTS = {
    "flutter": {".dart"},
    "node":    {".ts", ".tsx", ".js", ".jsx"},
    "python":  {".py"},
    "go":      {".go"},
    "rust":    {".rs"},
    "java":    {".java", ".kt"},
    "android": {".java", ".kt", ".xml"},
    "ios":     {".swift", ".m", ".h"},
    "unknown": {".dart", ".ts", ".py", ".go", ".rs", ".java", ".kt"},
}


# ── Keyword extraction ────────────────────────────────────────────────────────

def extract_keywords(task: str, min_len: int = 4, max_count: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{%d,}" % (min_len - 1), task)
    cleaned = []
    seen = set()
    for w in words:
        low = w.lower()
        if low in STOPWORDS or low in seen:
            continue
        seen.add(low)
        cleaned.append(w)
        if len(cleaned) >= max_count:
            break
    return cleaned


# ── Grep-based anchor discovery ───────────────────────────────────────────────

def grep_files(root: Path, keywords: list[str], exts: set[str]) -> list[Path]:
    """Use git grep (fast + respects .gitignore) else fall back to rg or python."""
    if not keywords:
        return []

    pattern = "|".join(re.escape(k) for k in keywords)

    # Try git grep first (respects .gitignore automatically)
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "grep", "-l", "-I", "-E", pattern],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode in (0, 1):
            files = [root / line.strip() for line in r.stdout.splitlines()
                     if line.strip()]
            filtered = [f for f in files if f.suffix in exts and f.is_file()]
            if filtered:
                return filtered
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: ripgrep
    try:
        r = subprocess.run(
            ["rg", "-l", "-i", "--no-ignore-vcs", "-e", pattern, str(root)],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode in (0, 1):
            files = [Path(line.strip()) for line in r.stdout.splitlines()
                     if line.strip()]
            return [f for f in files if f.suffix in exts and f.is_file()][:40]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Last resort: Python scan (slow)
    matches = []
    for f in root.rglob("*"):
        if f.suffix not in exts or not f.is_file():
            continue
        try:
            if re.search(pattern, f.read_text(encoding="utf-8", errors="ignore"), re.IGNORECASE):
                matches.append(f)
        except Exception:
            continue
        if len(matches) >= 40:
            break
    return matches


# ── Import extraction (Dart/TS/Python) ───────────────────────────────────────

_IMPORT_PATTERNS = {
    ".dart":  re.compile(r"""import\s+['"]([^'"]+)['"]"""),
    ".ts":    re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]"""),
    ".tsx":   re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]"""),
    ".js":    re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]"""),
    ".jsx":   re.compile(r"""(?:from|import)\s+['"]([^'"]+)['"]"""),
    ".py":    re.compile(r"""^\s*(?:from\s+(\S+)\s+import|import\s+(\S+))""", re.MULTILINE),
    ".go":    re.compile(r"""import\s+(?:\(([^)]+)\)|['"]([^'"]+)['"])"""),
}


def resolve_imports(file: Path, root: Path, project_kind: str) -> list[Path]:
    """Return existing files imported by `file` (1 level deep)."""
    pattern = _IMPORT_PATTERNS.get(file.suffix)
    if not pattern:
        return []
    try:
        text = file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    imports: list[Path] = []
    for m in pattern.finditer(text):
        # Extract the matched import path from whichever group captured it
        for g in m.groups():
            if not g:
                continue
            path_str = g.strip()
            # Relative: resolve against file's parent
            if path_str.startswith("./") or path_str.startswith("../"):
                candidate = (file.parent / path_str).resolve()
                if candidate.exists() and candidate.is_file():
                    imports.append(candidate)
                else:
                    # try common extensions
                    for ext in (".dart", ".ts", ".tsx", ".js", ".jsx", ".py"):
                        c2 = candidate.with_suffix(ext)
                        if c2.exists():
                            imports.append(c2)
                            break
            # package: imports (Flutter)
            elif path_str.startswith("package:"):
                pkg_rel = path_str.split(":", 1)[1]  # e.g. myapp/features/login/bloc.dart
                parts = pkg_rel.split("/", 1)
                if len(parts) == 2:
                    candidate = root / "lib" / parts[1]
                    if candidate.exists():
                        imports.append(candidate)
            # Bare relative (node, python)
            else:
                for rel_base in ("src", "lib", "app"):
                    candidate = root / rel_base / path_str
                    if candidate.is_file():
                        imports.append(candidate)
                        break
                    for ext in (".ts", ".tsx", ".js", ".jsx", ".py"):
                        c2 = candidate.with_suffix(ext)
                        if c2.is_file():
                            imports.append(c2)
                            break
            break  # one path per match
    return imports[:8]


# ── File content extraction with budget ───────────────────────────────────────

def read_file_slice(path: Path, keywords: list[str], max_chars: int) -> str:
    """
    Read file content. If file > max_chars, extract only regions mentioning
    keywords (with ±20 lines context), fall back to head of file.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    if len(text) <= max_chars:
        return text

    if not keywords:
        return text[:max_chars] + f"\n... [truncated, full size {len(text)} chars]"

    lines = text.splitlines()
    keyword_re = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
    hot_lines: set[int] = set()
    for i, line in enumerate(lines):
        if keyword_re.search(line):
            hot_lines.update(range(max(0, i - 20), min(len(lines), i + 21)))

    if not hot_lines:
        return text[:max_chars] + f"\n... [truncated, no keyword hit in {len(text)} chars]"

    # Build regions with gaps
    sorted_lines = sorted(hot_lines)
    out_lines = []
    budget = max_chars
    prev = -2
    for ln in sorted_lines:
        if ln != prev + 1 and out_lines:
            out_lines.append("    ...")
        line_text = lines[ln]
        if budget < len(line_text):
            out_lines.append("    ... [budget exhausted]")
            break
        out_lines.append(line_text)
        budget -= len(line_text) + 1
        prev = ln
    return "\n".join(out_lines)


# ── Main entry ────────────────────────────────────────────────────────────────

def build_scoped_context(
    project: ProjectInfo,
    task: str,
    *,
    max_total_chars: int = MAX_TOTAL_CHARS,
) -> str:
    """
    Produce a focused project context markdown for one task.

    Structure:
      # Project: <name>
      ## Stack / Paths
      ## Key config
      ## Map files (main/routes/DI/theme)
      ## Anchors (grep hits for task keywords)
      ## Imports (1-hop from anchors)
      ## Task scope summary
    """
    root = project.root
    kind = project.kind
    exts = SOURCE_EXTS.get(kind, SOURCE_EXTS["unknown"])
    keywords = extract_keywords(task)

    sections: list[str] = []
    budget = max_total_chars

    def _add(title: str, content: str) -> bool:
        nonlocal budget
        block = f"\n## {title}\n{content}\n"
        if len(block) > budget:
            block = block[: max(0, budget)] + "\n...[budget reached]\n"
            sections.append(block)
            budget = 0
            return False
        sections.append(block)
        budget -= len(block)
        return True

    # ── Header ───────────────────────────────────────────────────────────────
    sections.append(
        f"# Project: {project.name}\n"
        f"**Path:** `{root}`\n**Kind:** {kind}\n**Git root:** {project.git_root}\n"
        f"**Task keywords:** {', '.join(keywords) or '(none extracted)'}\n"
    )
    budget -= len(sections[-1])

    # ── Key config files (truncated) ─────────────────────────────────────────
    cfg_files = {
        "flutter": ["pubspec.yaml", "analysis_options.yaml"],
        "node":    ["package.json", "tsconfig.json"],
        "python":  ["pyproject.toml", "requirements.txt"],
        "go":      ["go.mod"],
        "rust":    ["Cargo.toml"],
    }.get(kind, [])
    cfg_block = []
    for fname in cfg_files:
        f = root / fname
        if f.exists():
            content = read_file_slice(f, [], 3000)
            cfg_block.append(f"### {fname}\n```\n{content}\n```")
    if cfg_block and budget > 500:
        _add("Key Configuration", "\n\n".join(cfg_block))

    # ── Map files ────────────────────────────────────────────────────────────
    map_block = []
    map_candidates = MAP_FILES.get(kind, [])
    for rel in map_candidates:
        f = root / rel
        if f.exists() and not project.should_skip(f):
            content = read_file_slice(f, keywords, PER_FILE_MAX)
            map_block.append(f"### {rel}\n```{f.suffix.lstrip('.')}\n{content}\n```")
            budget -= len(content)
            if budget < 2000:
                break
    if map_block:
        _add("Map / Entry Files", "\n\n".join(map_block))

    # ── Anchor files (grep hits) ─────────────────────────────────────────────
    anchors: list[Path] = []
    if keywords and budget > 5000:
        anchor_matches = grep_files(root, keywords, exts)
        anchors = [a for a in anchor_matches if not project.should_skip(a)][:MAX_ANCHORS]

    anchor_block = []
    for f in anchors:
        rel = f.relative_to(root)
        content = read_file_slice(f, keywords, PER_FILE_MAX)
        block = f"### {rel}\n```{f.suffix.lstrip('.')}\n{content}\n```"
        if len(block) > budget:
            break
        anchor_block.append(block)
        budget -= len(block)
    if anchor_block:
        _add(f"Task-Relevant Files ({len(anchor_block)} anchors)",
             "\n\n".join(anchor_block))

    # ── Imports from anchors ─────────────────────────────────────────────────
    if anchors and budget > 2000:
        seen_paths: set[Path] = set(anchors)
        import_block = []
        for anchor in anchors[:6]:
            for imp in resolve_imports(anchor, root, kind):
                if imp in seen_paths or project.should_skip(imp):
                    continue
                seen_paths.add(imp)
                content = read_file_slice(imp, keywords, PER_FILE_MAX // 2)
                try:
                    rel = imp.relative_to(root)
                except ValueError:
                    rel = imp
                block = f"### {rel}\n```{imp.suffix.lstrip('.')}\n{content}\n```"
                if len(block) > budget:
                    break
                import_block.append(block)
                budget -= len(block)
                if len(import_block) >= MAX_IMPORTS:
                    break
            if budget < 2000 or len(import_block) >= MAX_IMPORTS:
                break
        if import_block:
            _add(f"Imports from anchors ({len(import_block)} files)",
                 "\n\n".join(import_block))

    # ── Scope summary ────────────────────────────────────────────────────────
    summary = (
        f"- Total files read: {len(map_block) + len(anchor_block) + (len(import_block) if 'import_block' in locals() else 0)}\n"
        f"- Budget remaining: {budget:,} chars of {max_total_chars:,}\n"
        f"- Strategy: keyword-scoped (anchors + 1-hop imports)\n"
    )
    sections.append(f"\n## Scope Summary\n{summary}\n")

    return "".join(sections)
