"""Tests for learning.rule_runner + skill_runner — confirm module split
preserved behaviour (back-compat shim) and key pure functions work."""
from learning.rule_runner import rule_path_for
from learning.runners import rule_path_for as shim_rpath  # back-compat
from learning.runners import run_skill_optimizer as _run_skill  # re-export


def test_rule_path_for_returns_expected():
    """Rule path is deterministic per (profile, agent, target_type)."""
    p = rule_path_for("default", "ba", "rule")
    assert p.name == "ba.md"
    assert p.parent.name == "default"

    c = rule_path_for("default", "dev", "criteria")
    assert c.parent.name == "criteria"


def test_back_compat_shim_re_exports():
    """Old `from learning.runners import X` must still work."""
    assert shim_rpath is rule_path_for
    assert callable(_run_skill)


def test_rule_path_for_non_criteria_goes_to_root():
    p = rule_path_for("myprofile", "techlead", "rule")
    assert str(p).endswith("myprofile/techlead.md")
