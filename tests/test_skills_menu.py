"""Tests for the LLM-self-routed skill menu (replaces the old Python picker).

Skills no longer get pre-selected by Python. Instead BaseAgent.system_prompt
appends a `WORKING MODES` block listing every skill, the LLM picks one and
emits `MODE: <name>` on the first line of its reply, and base_agent records
the choice into `_active_skills` + `_skill_usage_log` for the analyzer.
"""
from agents.base_agent import (
    BaseAgent, _summarise_skill, _parse_mode_tag, _render_skills_menu,
    _MODE_TAG_RE,
)


def test_summarise_drops_frontmatter_title_and_hint():
    raw = (
        "---\nSCOPE: bug_fix\nTRIGGERS: fix\n---\n\n"
        "# Skill Title\n\nBody line one.\n\n"
        "<!-- TOOL-USE-HINT v1 -->\nIgnore me.\n"
    )
    out = _summarise_skill(raw)
    assert "SCOPE:" not in out
    assert "Skill Title" not in out
    assert "TOOL-USE-HINT" not in out
    assert "Ignore me" not in out
    assert "Body line one." in out


def test_summarise_truncates_long_body():
    raw = "x" * 1000
    out = _summarise_skill(raw)
    assert len(out) <= 290   # 280 cap + " …" suffix


def test_parse_mode_tag_picks_first_line():
    assert _parse_mode_tag("MODE: bug_fix\nrest") == "bug_fix"
    assert _parse_mode_tag("  mode: feature\nbody") == "feature"
    assert _parse_mode_tag("nothing here") is None


def test_render_menu_lists_each_skill_with_scope_and_triggers():
    skills = [
        {"skill_key": "alpha", "scope": ["bug_fix"],
         "triggers": ["fix", "bug"], "content": "# T\nA body."},
        {"skill_key": "beta", "scope": ["feature"],
         "triggers": ["feature"], "content": "# T\nB body."},
    ]
    out = _render_skills_menu(skills)
    assert "WORKING MODES" in out
    assert "MODE:" in out          # instruction echoed
    assert "`alpha`" in out
    assert "`beta`" in out
    assert "scope: bug_fix" in out
    assert "triggers: fix, bug" in out


def test_record_mode_populates_active_skills(monkeypatch):
    """Round-trip: LLM reply with MODE tag → _active_skills updated."""

    class _Stub(BaseAgent):
        ROLE = "Stub"
        RULE_KEY = "ba"          # any existing rule
        SKILL_KEY = "ba"         # any existing skills folder

    a = _Stub()
    # Fake skill list containing one matching key
    monkeypatch.setattr(
        "pipeline.skill_selector.list_skills",
        lambda agent_key: [{
            "skill_key": "bug_fix",
            "scope": ["bug_fix"],
            "triggers": ["fix"],
            "content": "x",
        }],
    )
    a._record_mode_from_output("MODE: bug_fix\n\nrest of reply")
    assert len(a._active_skills) == 1
    assert a._active_skills[0]["skill_key"] == "bug_fix"
    assert a._active_skills[0]["selection_method"] == "llm_self"
    assert a._skill_usage_log[-1]["skill"] == "bug_fix"


def test_record_mode_falls_back_when_skill_unknown(monkeypatch):
    """Hallucinated skill names trigger the keyword-scorer fallback so we
    still record SOMETHING (with method='fallback_keyword')."""
    class _Stub(BaseAgent):
        ROLE = "Stub"
        RULE_KEY = "ba"
        SKILL_KEY = "ba"

    a = _Stub()
    fake = [{"skill_key": "real_one", "scope": ["feature"],
             "triggers": ["fix"], "content": "x"}]
    monkeypatch.setattr("pipeline.skill_selector.list_skills",
                        lambda agent_key: fake)
    monkeypatch.setattr(
        "pipeline.skill_selector.select_skills",
        lambda *a, **kw: [{"skill_key": "real_one", "scope": ["feature"],
                            "triggers": ["fix"], "content": "x"}],
    )
    a._record_mode_from_output(
        "MODE: hallucinated_skill\nbody mentions fix",
        task_text="please fix the login bug",
    )
    # Keyword fallback assigned a real skill, marked as fallback_keyword
    assert len(a._active_skills) == 1
    assert a._active_skills[0]["skill_key"] == "real_one"
    assert a._skill_usage_log[-1]["method"] == "fallback_keyword"


def test_detect_skill_is_noop_now():
    """Back-compat: detect_skill exists but no longer pre-warms anything."""
    class _Stub(BaseAgent):
        ROLE = "Stub"
        SKILL_KEY = "ba"

    a = _Stub()
    result = a.detect_skill("any task")
    assert result is None
    assert a._active_skills == []
