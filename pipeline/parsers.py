"""
Text parsers that extract structured signals from agent outputs.

All functions here are pure — they take a string and return data. No I/O,
no dependencies on orchestrator state. That makes them trivial to unit-test:

    assert extract_blockers("TC-1  severity: blocker") == ["TC-1  severity: blocker"]
    assert extract_missing_info("MISSING_INFO: auth flow — MUST_ASK: TechLead")

Moved out of `orchestrator.py` during Phase 4 refactor.
"""
from __future__ import annotations

import re


def extract_blockers(review_output: str) -> list[str]:
    """Parse BLOCKER lines from QA review output."""
    blockers: list[str] = []
    for line in review_output.splitlines():
        if re.search(r"severity:\s*blocker", line, re.IGNORECASE):
            blockers.append(line.strip())
        elif "BLOCKER" in line and line.strip().startswith("TC-"):
            blockers.append(line.strip())
    return blockers


# `ASK_USER: <question>` — agent emits this when the request is too
# ambiguous to act on. Pipeline pauses, asks the user, re-runs the agent
# with the answer appended to the original task. Multiple ASK_USER lines
# in one reply are aggregated into a single question batch.
_ASK_USER_RE = re.compile(r"(?im)^\s*ASK_USER\s*:\s*(.+?)\s*$")


def extract_ask_user(output: str) -> list[str]:
    """Pull every `ASK_USER: ...` line the agent emitted. Empty list if
    none — caller can skip the clarification round in that case."""
    if not output:
        return []
    return [m.group(1).strip() for m in _ASK_USER_RE.finditer(output)
            if m.group(1).strip()]


# Patterns for missing-widget-key detection, compiled once at import time
# rather than inside the hot loop.
_MISSING_KEY_PAT_1 = re.compile(
    r"[Mm]issing\s+key[:\s]+[Kk]ey\(['\"]([^'\"]+)['\"]\)"
    r"(?:\s+in\s+(\w+))?"
    r"(?:\s*\(([^)]+)\))?"
    r"(?:\s*[—-]\s*(.+))?",
)
_MISSING_KEY_PAT_2 = re.compile(
    r"[Nn]eed\s+widget\s+key[:\s]+['\"]([^'\"]+)['\"]"
    r"(?:\s+on\s+(\w+))?"
    r"(?:\s+in\s+([^\s]+))?"
    r"(?:\s+for\s+(.+))?",
)


def extract_missing_widget_keys(review_output: str) -> list[dict]:
    """
    Parse QA review for missing widget keys.

    Detects lines like::

        - Missing key: Key('login_email') in TextField (lib/auth/login.dart) — email input
        - Need widget key: 'submit_btn' on ElevatedButton for login submit action

    Returns ``[{"key": ..., "widget_type": ..., "file_hint": ..., "purpose": ...}]``,
    deduplicated by key name.
    """
    missing: list[dict] = []
    for line in review_output.splitlines():
        for pat in (_MISSING_KEY_PAT_1, _MISSING_KEY_PAT_2):
            m = pat.search(line)
            if m:
                missing.append({
                    "key":         m.group(1).strip(),
                    "widget_type": (m.group(2) or "widget").strip(),
                    "file_hint":   (m.group(3) or "").strip(),
                    "purpose":     (m.group(4) or "").strip(),
                })
                break

    seen: set[str] = set()
    out: list[dict] = []
    for item in missing:
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        out.append(item)
    return out


def extract_fixes_required(review_output: str) -> list[str]:
    """Extract lines from the FIXES REQUIRED section of a QA review."""
    lines = review_output.splitlines()
    collecting = False
    fixes: list[str] = []
    for line in lines:
        stripped = line.strip()
        if "FIXES REQUIRED" in stripped.upper():
            collecting = True
            continue
        if collecting:
            if stripped.startswith("##"):
                break
            if stripped and not stripped.startswith("#"):
                fixes.append(stripped.lstrip("-•* "))
    return [f for f in fixes if f]


_MISSING_INFO_RE = re.compile(
    r"MISSING_INFO:\s*(.+?)(?:\s*[—-]+\s*MUST_ASK:\s*(.+))?$"
)


def extract_missing_info(text: str) -> list[dict]:
    """
    Parse ``MISSING_INFO: [what] — MUST_ASK: [who]`` lines from agent output.
    The ``MUST_ASK`` suffix is optional; defaults to ``"User"``.
    """
    items: list[dict] = []
    for line in text.splitlines():
        m = _MISSING_INFO_RE.match(line.strip())
        if m:
            items.append({
                "info":   m.group(1).strip(),
                "source": (m.group(2) or "User").strip(),
            })
    return items
