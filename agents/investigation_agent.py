"""Investigation Agent - Reads codebase and summarizes relevant context before Dev/TechLead work."""
from __future__ import annotations
import re
from pathlib import Path
from core.logging import tprint
from .base_agent import BaseAgent

_SYSTEM = """You are a Senior Code Investigator specializing in reading and analyzing existing codebases.

Nhiệm vụ:
- Xác định các file liên quan đến task hiện tại
- Đọc và tóm tắt implementation hiện tại
- Phát hiện bugs, inconsistencies, hoặc gaps so với request mới
- Cung cấp context đầy đủ để Dev/TechLead làm việc KHÔNG phải đoán mò

Nguyên tắc:
- Chỉ báo cáo những gì THỰC SỰ tồn tại in code, không suy đoán
- Nếu file không tồn tại, ghi rõ "MISSING"
- Ghi rõ version/pattern hiện tại để Dev biết cần follow hay refactor"""


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
        identify_prompt = f"""Dựa trên project context dưới đây, liệt kê các file CẦN ĐỌC để thực hiện task này.
Chỉ liệt kê file paths, mỗi file một dòng, bắt đầu bằng "FILE: ".

=== TASK ===
{task_description}

=== PROJECT CONTEXT ===
{self.project_context}

Chỉ liệt kê max 8 file quan trọng nhất. Format: FILE: path/to/file.dart"""

        raw_files = self._call(_SYSTEM, identify_prompt)
        file_paths = re.findall(r"FILE:\s*(.+)", raw_files)

        # Step 2: read files from disk
        file_contents = self._read_files(file_paths)

        # Step 3: produce investigation report
        files_block = "\n\n".join(
            f"### {path}\n```\n{content}\n```"
            for path, content in file_contents.items()
        )

        report_prompt = f"""Phân tích code hiện có và tạo investigation report cho task:

=== TASK ===
{task_description}

=== TECH CONTEXT ===
{tech_context}

=== CURRENT CODE ===
{files_block if files_block else "(không tìm thấy file liên quan)"}

Tạo report theo format BẮT BUỘC:

## 🔍 INVESTIGATION REPORT

### 📁 Files Liên Quan
[mỗi file: path | trạng thái (EXISTS/MISSING) | vai trò trong task]

### 📊 Current Implementation
[mô tả ngắn gọn implementation hiện tại, pattern sử dụng, version library]

### ⚠️ Issues & Gaps
ISSUE: [vấn đề cụ thể tìm thấy] — FILE: [tên file] LINE: [dòng nếu biết]
GAP: [thiếu gì so với task request]

### 🔗 Dependencies & Integration Points
[các module/service mà task này sẽ ảnh hưởng hoặc phụ thuộc]

### 💡 Recommendation
[nên implement theo hướng nào, follow pattern hiện tại hoặc cần refactor]"""

        return self._call(_SYSTEM, report_prompt)

    def _resolve_base_dirs(self) -> list[Path]:
        """
        Find project root from multiple sources (robust fallback chain):
        1. project_context lines with common path markers
        2. Absolute paths found anywhere in project_context
        3. Current working directory
        """
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

    @staticmethod
    def _safe_read(path: Path, base_dirs: list[Path]) -> str | None:
        """Read file only if it resolves within one of the allowed base dirs."""
        try:
            resolved = path.resolve()
            if not resolved.is_file():
                return None
            for base in base_dirs:
                try:
                    resolved.relative_to(base.resolve())
                    return resolved.read_text(encoding="utf-8", errors="ignore")
                except ValueError:
                    continue
        except OSError:
            pass
        return None

    def _read_files(self, file_paths: list[str]) -> dict[str, str]:
        """Try to read each file path from the project context directory."""
        result = {}
        base_dirs: list[Path] = self._resolve_base_dirs()

        seen: set[str] = set()

        for raw_path in file_paths[:8]:
            raw_path = raw_path.strip()
            if raw_path in seen:
                continue
            seen.add(raw_path)

            # Try absolute path (validated against base_dirs)
            content = self._safe_read(Path(raw_path), base_dirs)

            if content is None:
                # Try relative to each base dir
                for base in base_dirs:
                    content = self._safe_read(base / raw_path, base_dirs)
                    if content is not None:
                        break

            result[raw_path] = content if content is not None else "(file not found on disk)"

        return result

    def print_report(self, report: str):
        tprint(f"\n  {'─'*60}")
        tprint(f"  🔍 INVESTIGATION REPORT")
        tprint(f"  {'─'*60}")
        for line in report.splitlines()[:40]:
            tprint(f"  {line}")
        if report.count("\n") > 40:
            tprint(f"  ... [truncated — full report saved to checkpoint]")
        tprint(f"  {'─'*60}")
