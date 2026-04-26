"""Pure text-manipulation helpers used by orchestrator and agents.

These functions are intentionally side-effect-free so they can be unit tested
without instantiating any agents.
"""
from __future__ import annotations


def smart_trim(text: str, max_chars: int, keep_headers: bool = True) -> str:
    """
    Smart truncation: keeps markdown headers and first paragraph of each section.
    Falls back to simple truncation when text is short enough.
    """
    if len(text) <= max_chars:
        return text

    if not keep_headers:
        return text[:max_chars] + "\n\n...[truncated]"

    lines = text.splitlines()
    result: list[str] = []
    budget = max_chars

    for line in lines:
        entry = line + "\n"
        if len(entry) > budget:
            if budget > 40:
                result.append(line[:budget] + "...")
            break
        result.append(entry)
        budget -= len(entry)
        if budget <= 0:
            break

    return "".join(result)


def extract_section(text: str, *keywords: str, max_chars: int = 800) -> str:
    """Extract the first section whose header matches any keyword."""
    lines = text.splitlines()
    collecting = False
    result: list[str] = []
    budget = max_chars

    for line in lines:
        stripped = line.lstrip("#").strip().lower()
        if any(kw.lower() in stripped for kw in keywords):
            collecting = True
        elif line.startswith("#") and collecting:
            break  # next section — stop

        if collecting:
            result.append(line)
            budget -= len(line) + 1
            if budget <= 0:
                result.append("...[truncated]")
                break

    return "\n".join(result) if result else smart_trim(text, max_chars)


# Back-compat aliases — orchestrator.py and any external callers still import
# the underscore-prefixed names.
_smart_trim = smart_trim
_extract_section = extract_section
