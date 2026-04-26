"""Tests for pm_agent.classify — heuristic + LLM step decider behaviour.

DISPATCH_PLAN got removed: every non-investigation kind now goes through the
LLM step picker so the flow can adapt per-request. Only `investigation`
short-circuits to its single fixed step.
"""
from agents.pm_agent import (
    PMAgent, KIND_BUG_FIX, KIND_UI_TWEAK, KIND_INVESTIGATION,
    KIND_FEATURE, INVESTIGATION_STEPS,
)


class _CallCounter:
    def __init__(self, response: str = "STEPS: dev, test"):
        self.calls = 0
        self.response = response

    def __call__(self, system, user):
        self.calls += 1
        return self.response


def _pm_with_fake_call(response: str = "STEPS: dev, test"):
    pm = PMAgent()
    counter = _CallCounter(response=response)
    pm._call = counter
    return pm, counter


def test_bug_fix_uses_llm_step_picker():
    """Heuristic classifies bug_fix → still asks LLM which steps to run."""
    pm, counter = _pm_with_fake_call("STEPS: dev, test")
    d = pm.classify("fix login crash khi email empty")
    assert d.kind == KIND_BUG_FIX
    assert d.dynamic_steps == ["dev", "test"]
    assert counter.calls == 1


def test_ui_tweak_uses_llm_step_picker():
    pm, counter = _pm_with_fake_call("STEPS: design, dev, test")
    d = pm.classify("doi mau nut save thanh xanh")
    assert d.kind == KIND_UI_TWEAK
    assert d.dynamic_steps == ["design", "dev", "test"]
    assert counter.calls == 1


def test_investigation_short_circuits_no_llm():
    """Investigation has its own single-step flow — never call LLM picker."""
    pm, counter = _pm_with_fake_call()
    d = pm.classify("giai thich how does authentication work trong module nay")
    assert d.kind == KIND_INVESTIGATION
    assert d.dynamic_steps == INVESTIGATION_STEPS
    assert counter.calls == 0


def test_feature_uses_llm_for_dynamic_steps():
    pm, counter = _pm_with_fake_call("STEPS: ba, design, techlead, dev, test")
    d = pm.classify("them tinh nang OAuth login voi Google va Apple")
    assert d.kind == KIND_FEATURE
    # 1 call = step decider (heuristic confident enough → no classification call)
    # OR 2 calls = classification + step decider. Either is acceptable —
    # what we care about is dev appears in the chosen plan.
    assert "dev" in d.dynamic_steps
    assert counter.calls >= 1


def test_step_picker_falls_back_to_all_when_llm_fails():
    """If LLM returns garbage, fall back to ALL_STEPS (don't silently drop steps)."""
    pm, counter = _pm_with_fake_call(response="??? not a step list")
    d = pm.classify("them tinh nang dashboard analytics")
    assert "dev" in d.dynamic_steps
    assert "ba" in d.dynamic_steps
