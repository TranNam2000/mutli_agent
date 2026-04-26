"""Pure-function tests for pipeline.parsers — quickest to verify."""
from pipeline.parsers import (
    extract_blockers,
    extract_missing_widget_keys,
    extract_fixes_required,
    extract_missing_info,
)


def test_extract_blockers_severity_and_tc():
    out = (
        "TC-001 passes\n"
        "TC-002  severity: BLOCKER — login crashes\n"
        "TC-003 BLOCKER because of missing validation\n"
        "random line\n"
    )
    blockers = extract_blockers(out)
    assert len(blockers) == 2
    assert any("TC-002" in b for b in blockers)
    assert any("TC-003" in b for b in blockers)


def test_extract_missing_widget_keys_dedup():
    out = (
        "- Missing key: Key('login_email') in TextField (lib/auth/login.dart) — email\n"
        "- Missing key: Key('login_email') in TextField — duplicate should dedupe\n"
        "- Need widget key: 'submit_btn' on ElevatedButton in lib/auth.dart for submit\n"
    )
    keys = extract_missing_widget_keys(out)
    assert len(keys) == 2
    names = {k["key"] for k in keys}
    assert names == {"login_email", "submit_btn"}


def test_extract_fixes_required_stops_at_next_section():
    out = (
        "## INTRO\nnothing here\n\n"
        "## FIXES REQUIRED\n"
        "- fix button color\n"
        "- add null check\n"
        "- validate email format\n\n"
        "## NEXT STEPS\n- release\n"
    )
    fixes = extract_fixes_required(out)
    assert fixes == ["fix button color", "add null check", "validate email format"]


def test_extract_missing_info_with_and_without_source():
    out = (
        "Some output here.\n"
        "MISSING_INFO: OAuth timeout policy — MUST_ASK: TechLead\n"
        "MISSING_INFO: password complexity rules\n"
    )
    items = extract_missing_info(out)
    assert len(items) == 2
    assert items[0]["source"] == "TechLead"
    assert items[1]["source"] == "User"   # default
