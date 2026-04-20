"""
ContextBuilder — extract the right slice of each artifact for each agent.

Each agent has different information needs:
  Design   ← BA: personas + user flows + must-have features
  TechLead ← BA: functional reqs + non-functional reqs + constraints
  Dev      ← TechLead: API contracts + data models + coding standards
  Dev      ← Design: component specs + screen flows for the target story
  QA       ← BA: acceptance criteria + risks
  QA       ← TechLead: API contracts + security considerations
  QA       ← Dev: implemented modules + known limitations
"""
from __future__ import annotations
import re
import subprocess
import tempfile
from pathlib import Path

SUMMARIZE_THRESHOLD = 3000  # chars — above this, summarize with LLM


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _sections(text: str) -> dict[str, str]:
    """Parse markdown into {header_text_lower: section_body} dict."""
    result: dict[str, str] = {}
    current_header = "__intro__"
    current_lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,3} ", line):
            result[current_header] = "\n".join(current_lines).strip()
            current_header = line.lstrip("#").strip().lower()
            current_lines = []
        else:
            current_lines.append(line)
    result[current_header] = "\n".join(current_lines).strip()
    return result


def _llm_summarize(text: str, focus: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Summarize long context using LLM, preserving focus-relevant details."""
    system = (
        f"Tóm tắt content after to truyền for agent need thông tin về: {focus}. "
        "Giữ again toàn bộ: number liệu cụ can, API endpoint, acceptance criteria, "
        "quyết định technical, constraint, risk. Bỏ phần giải thích thừa. "
        "Output dạng bullet points concise, đầy enough thông tin."
    )
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(system)
        tmp.flush()
        tmp.close()
        import os as _os
        clean_env = {k: v for k, v in _os.environ.items()
                       if k != "ANTHROPIC_API_KEY"}
        result = subprocess.run(
            ["claude", "-p", text, "--system-prompt-file", tmp.name,
             "--output-format", "text", "--bare"],
            capture_output=True, text=True, env=clean_env, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    finally:
        if tmp:
            Path(tmp.name).unlink(missing_ok=True)
    return text


def _build(source: str, focus: str, *keyword_groups: tuple[str, ...]) -> str:
    """
    Extract relevant sections from source, deduplicate, then summarize if too long.
    Falls back to full source if no keywords match at all.
    """
    seen_headers: set[str] = set()
    parts: list[str] = []

    for keywords in keyword_groups:
        secs = _sections(source)
        for header, body in secs.items():
            if header in seen_headers:
                continue
            if any(kw.lower() in header for kw in keywords):
                parts.append(f"### {header.title()}\n{body}")
                seen_headers.add(header)

    result = "\n\n".join(parts) if parts else source
    if len(result) > SUMMARIZE_THRESHOLD:
        result = _llm_summarize(result, focus)
    return result


# ── Per-agent context extractors ──────────────────────────────────────────────

class ContextBuilder:

    # ── Design ────────────────────────────────────────────────────────────────
    @staticmethod
    def for_design_from_ba(prd: str) -> str:
        return _build(prd, "personas, user flows, tính năng bắt buộc, accessibility",
            ("persona", "user", "stakeholder", "user", "stakeholder"),
            ("journey", "flow", "use case", "luồng", "hành trình"),
            ("must", "functional", "feature", "chức năng", "tính năng"),
            ("non-functional", "constraint", "accessibility", "ràng buộc"),
        )

    # ── TechLead ──────────────────────────────────────────────────────────────
    @staticmethod
    def for_techlead_from_ba(prd: str) -> str:
        return _build(prd, "functional reqs, performance, security, constraints, risks",
            ("functional", "requirement", "feature", "chức năng", "request"),
            ("non-functional", "performance", "security", "scalab", "phi chức năng"),
            ("constraint", "assumption", "ràng buộc", "giả định"),
            ("risk", "rủi ro"),
        )

    # ── Dev ───────────────────────────────────────────────────────────────────
    @staticmethod
    def for_dev_from_techlead(tech: str) -> str:
        return _build(tech, "API contracts, data models, folder structure, coding standards, security",
            ("api", "endpoint", "contract"),
            ("database", "schema", "model", "entity", "cơ sở dữ liệu"),
            ("folder", "structure", "project", "cấu trúc"),
            ("coding", "standard", "convention", "quy ước"),
            ("security", "bảo mật"),
        )

    @staticmethod
    def for_dev_from_design(design: str) -> str:
        return _build(design, "component specs, screen flows, animations",
            ("component", "spec", "thành phần"),
            ("screen", "wireframe", "flow", "màn hình", "luồng"),
            ("animation", "interaction", "hiệu ứng"),
        )

    # ── QA ────────────────────────────────────────────────────────────────────
    @staticmethod
    def for_qa_from_ba(prd: str) -> str:
        return _build(prd, "acceptance criteria, risks, non-functional requirements",
            ("acceptance", "criteria", "tiêu chí", "điều kiện"),
            ("risk", "assumption", "rủi ro", "giả định"),
            ("non-functional", "performance", "security", "phi chức năng"),
        )

    @staticmethod
    def for_qa_from_techlead(tech: str) -> str:
        return _build(tech, "API endpoints, security, performance targets, risks",
            ("api", "endpoint"),
            ("security", "bảo mật"),
            ("performance", "hiệu năng"),
            ("risk", "rủi ro"),
        )

    @staticmethod
    def for_qa_from_dev(impl: str) -> str:
        return _build(impl, "implementation summary, limitations, error handling",
            ("implementation", "plan", "triển khai"),
            ("limitation", "todo", "known", "hạn chế"),
            ("error", "exception", "handle", "bug"),
        )
