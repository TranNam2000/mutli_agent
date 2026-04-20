"""Investigation Agent - Reads codebase and summarizes relevant context before Dev/TechLead work."""
from __future__ import annotations
import re
from pathlib import Path
from .base_agent import BaseAgent

_SYSTEM = """You is a Senior Code Investigator chuyên đọc and analyze codebase current có.

Nhiệm vụ:
- Xác định các file liên quan đến task  need thực current
- Đọc and tóm tắt implementation current tại
- Phát current bugs, inconsistencies, or gaps so with request new
- Cung cấp context enough to Dev/TechLead do việc KHÔNG need đoán mò

Principle:
- Chỉ báo cáo những gì THỰC SỰ tồn tại in code, no suy đoán
- If a file no tồn tại, ghi rõ "MISSING"
- Ghi rõ version/pattern current tại to Dev biết need follow or refactor"""


class InvestigationAgent(BaseAgent):
    ROLE = "Code Investigator"
    RULE_KEY = ""  # uses inline system prompt above

    def investigate(self, task_description: str, tech_context: str = "") -> str:
        """
        Step 1: Ask Claude which files are relevant.
        Step 2: Read those files from disk.
        Step 3: Produce structured investigation report.
        """
        if not self.project_context:
            return ""

        # Step 1: identify relevant file paths
        identify_prompt = f"""Dựa trên project context dưới here, liệt kê các file CẦN ĐỌC to thực current task này.
Chỉ liệt kê file paths, mỗi file a dòng, bắt đầu bằng "FILE: ".

=== TASK ===
{task_description}

=== PROJECT CONTEXT ===
{self.project_context}

Chỉ liệt kê max 8 file important nhất. Format: FILE: path/to/file.dart"""

        raw_files = self._call(_SYSTEM, identify_prompt, max_tokens=600)
        file_paths = re.findall(r"FILE:\s*(.+)", raw_files)

        # Step 2: read files from disk
        file_contents = self._read_files(file_paths)

        # Step 3: produce investigation report
        files_block = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in file_contents.items()
        )

        report_prompt = f"""Analyze code current có and tạo investigation report for task:

=== TASK ===
{task_description}

=== TECH CONTEXT ===
{tech_context}

=== CURRENT CODE ===
{files_block if files_block else "(no tìm thấy file liên quan)"}

Tạo report per format BẮT BUỘC:

## 🔍 INVESTIGATION REPORT

### 📁 Files Liên Quan
[mỗi file: path | trạng thái (EXISTS/MISSING) | vai trò in task]

### 📊 Current Implementation
[mô tả concise implementation current tại, pattern  use, version library]

### ⚠️ Issues & Gaps
ISSUE: [vấn đề cụ can tìm thấy] — FILE: [name file] LINE: [dòng if biết]
GAP: [thiếu gì so with task request]

### 🔗 Dependencies & Integration Points
[các module/service mà task này will ảnh hưởng or phụ thuộc]

### 💡 Recommendation
[nên implement per hướng nào, follow pattern current tại or need refactor]"""

        return self._call(_SYSTEM, report_prompt, max_tokens=2500)

    def _resolve_base_dirs(self) -> list[Path]:
        """
        Find project root from multiple sources (robust fallback chain):
        1. project_context lines with common path markers
        2. Absolute paths found anywhere in project_context
        3. Current working directory
        """
        import re
        dirs: list[Path] = []

        # Strategy 1: look for path-like markers across all lines (not just first 5)
        path_patterns = [
            r"^#\s*(?:Project|Directory|Path|Root|Base):\s*(.+)",  # "# Project: /path"
            r"^Project:\s*(.+)",
            r"^Directory:\s*(.+)",
            r"Scanned:\s*(.+)",
            r"^> (.+)",  # some context formats use "> /path"
        ]
        for line in self.project_context.splitlines()[:30]:
            for pat in path_patterns:
                m = re.match(pat, line.strip(), re.IGNORECASE)
                if m:
                    candidate = Path(m.group(1).strip())
                    if candidate.is_dir() and candidate not in dirs:
                        dirs.append(candidate)

        # Strategy 2: scan for absolute path strings that exist on disk
        for match in re.finditer(r"(/[/\w.\-]+)", self.project_context):
            candidate = Path(match.group(1))
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)

        # Strategy 3: fallback to cwd
        cwd = Path.cwd()
        if cwd not in dirs:
            dirs.append(cwd)

        return dirs

    def _read_files(self, file_paths: list[str]) -> dict[str, str]:
        """Try to read each file path from the project context directory."""
        result = {}
        base_dirs: list[Path] = self._resolve_base_dirs()

        seen: set[str] = set()  # dedup

        for raw_path in file_paths[:8]:
            raw_path = raw_path.strip()
            if raw_path in seen:
                continue
            seen.add(raw_path)
            content = None

            # Try absolute path first
            abs_p = Path(raw_path)
            if abs_p.exists() and abs_p.is_file():
                content = abs_p.read_text(encoding="utf-8", errors="ignore")
            else:
                # Try relative to each base dir
                for base in base_dirs:
                    candidate = base / raw_path
                    if candidate.exists() and candidate.is_file():
                        content = candidate.read_text(encoding="utf-8", errors="ignore")
                        break

            if content:
                # Trim large files
                result[raw_path] = content
            else:
                result[raw_path] = "(file not found on disk)"

        return result

    def print_report(self, report: str):
        print(f"\n  {'─'*60}")
        print(f"  🔍 INVESTIGATION REPORT")
        print(f"  {'─'*60}")
        for line in report.splitlines()[:40]:
            print(f"  {line}")
        if report.count("\n") > 40:
            print(f"  ... [truncated — full report saved to checkpoint]")
        print(f"  {'─'*60}")
