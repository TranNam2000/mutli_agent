"""
Flow tests for pipeline.task_based_runner.

We can't exercise the whole `run_task_based_pipeline` without standing up a
full orchestrator (agents, session manager, html report, …), so instead we
target two high-value sub-flows that exercise the actual production code
paths with small, focused stubs:

  1. `qa_dev_loop`                 — blocker parse + fix-loop termination
  2. `option_c_spec_postmortem`    — BA spec rewrite rescue branch

These are the routes that were previously only covered by end-to-end runs
with a real LLM. Stubbing the orchestrator surface keeps us honest: any
refactor that changes the expected orch API trips these tests.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline.task_based_runner import qa_dev_loop, option_c_spec_postmortem


# ── Shared stubs ─────────────────────────────────────────────────────────────

class _StubQA:
    ROLE = "QA"
    def __init__(self, review_outputs: list[str]):
        self._queue = list(review_outputs)
        self.calls = 0
        self._current_step = None
    def review_implementation(self, test_plan, impl, clar):
        self.calls += 1
        return self._queue.pop(0) if self._queue else "no blockers"


class _StubDev:
    ROLE = "Dev"
    def __init__(self, fix_outputs: list[str] | None = None):
        self._queue = list(fix_outputs or [])
        self.revise_calls = 0
        self.inject_calls = 0
        self._current_step = None
    def revise(self, impl, guide, original):
        self.revise_calls += 1
        return self._queue.pop(0) if self._queue else f"{impl} fixed"
    def inject_widget_keys(self, impl, keys):
        self.inject_calls += 1
        return impl + "\n// widget keys injected"


class _StubTL:
    ROLE = "Tech Lead"
    def __init__(self, triage_text: str = "TL: please fix X"):
        self._triage_text = triage_text
        self.triage_calls = 0
        self.ask_calls = 0
        self._ask_reply = "Spec missed edge case X; please add AC for empty state."
    def triage_bugs(self, dev, blockers, impl):
        self.triage_calls += 1
        return self._triage_text
    def ask(self, other_agent, question):
        self.ask_calls += 1
        return self._ask_reply


class _StubBA:
    ROLE = "BA"
    def __init__(self):
        self.revise_specs_calls = 0
    def revise_specs(self, current_md, reflection, stuck_ids):
        self.revise_specs_calls += 1
        return current_md + "\n\n## TASK-999 (new AC added)"


class _StubOrch:
    """Minimum surface consumed by qa_dev_loop + option_c_spec_postmortem."""
    def __init__(self):
        self.results = {"ba": "## TASK-001 | type=ui | priority=P1\n**Title:** X"}
        self.saved: list[tuple[str, str]] = []
        self._skipped_critic_by_task = {}
        self._emergency_audit = False
        self.emergency_audit_calls = 0
        self.session_id = "20260101_120000_abcd"
        self.agents = {}           # populated per-test
        self._ba_postmortem_fired = False
        # QA→Dev→TL loop reads these:
        self._quota_allowed = True
        self._step_token_status_calls = 0

    def _check_quota(self, label):
        return self._quota_allowed
    def _save(self, key, content):
        self.saved.append((key, content))
        self.results[key] = content
    def _extract_blockers(self, review_output):
        from pipeline.parsers import extract_blockers
        return extract_blockers(review_output)
    def _extract_missing_widget_keys(self, review_output):
        return []
    def _extract_fixes_required(self, review_output):
        return []
    def _trigger_emergency_audit(self, blockers, tasks, agent_in_charge="Dev"):
        self.emergency_audit_calls += 1
        self._emergency_audit = True
    def _dialogue_header(self, label):
        pass
    def _step_token_status(self, role):
        self._step_token_status_calls += 1


# ── qa_dev_loop tests ────────────────────────────────────────────────────────

def test_qa_dev_loop_no_blockers_exits_immediately():
    """Happy path — no BLOCKER in QA output → loop returns the original
    implementation without calling Dev or TL."""
    orch = _StubOrch()
    qa = _StubQA(review_outputs=[])
    dev = _StubDev()
    tl  = _StubTL()

    out = qa_dev_loop(
        orch, qa=qa, dev=dev, tl=tl,
        test_plan_doc="plan",
        implementation="impl-v1",
        dev_clarification="",
        initial_review="All tests pass. No blockers.",
        product_idea="build X",
        tasks=[],
    )

    assert out == "impl-v1"
    assert dev.revise_calls == 0
    assert tl.triage_calls == 0
    assert qa.calls == 0                     # never re-verify if no blockers


def test_qa_dev_loop_fixes_blockers_in_one_round():
    """One round: blocker on first review, clean on second → Dev fixes once."""
    orch = _StubOrch()
    # First review reported a blocker, re-verify round sees a clean run.
    qa = _StubQA(review_outputs=["All tests pass. No blockers."])
    dev = _StubDev(fix_outputs=["impl-fixed"])
    tl  = _StubTL()

    initial = "TC-1 severity: blocker — login fails on empty email"
    out = qa_dev_loop(
        orch, qa=qa, dev=dev, tl=tl,
        test_plan_doc="plan",
        implementation="impl-v1",
        dev_clarification="",
        initial_review=initial,
        product_idea="build X",
        tasks=[],
    )

    assert out == "impl-fixed"
    assert dev.revise_calls == 1
    assert tl.triage_calls == 1
    assert qa.calls == 1                     # re-verified once


def test_qa_dev_loop_triggers_emergency_audit_when_critic_skipped():
    """If orchestrator recorded critic-skip for tasks, a blocker on round 0
    must trigger an emergency audit exactly once."""
    orch = _StubOrch()
    orch._skipped_critic_by_task = {"TASK-001": ["techlead"]}  # truthy = skipped upstream
    qa = _StubQA(review_outputs=["no blockers"])  # round 2 clean
    dev = _StubDev(fix_outputs=["impl-fixed"])
    tl  = _StubTL()

    out = qa_dev_loop(
        orch, qa=qa, dev=dev, tl=tl,
        test_plan_doc="plan",
        implementation="impl-v1",
        dev_clarification="",
        initial_review="TC-1 severity: blocker",
        product_idea="build X",
        tasks=[],
    )

    assert orch.emergency_audit_calls == 1   # fired exactly once
    assert orch._emergency_audit is True
    assert out == "impl-fixed"


def test_qa_dev_loop_stops_when_quota_exhausted():
    """If _check_quota returns False at the start of a fix round, the loop
    must break without running Dev.revise."""
    orch = _StubOrch()
    orch._quota_allowed = False
    qa = _StubQA(review_outputs=[])
    dev = _StubDev()
    tl  = _StubTL()

    out = qa_dev_loop(
        orch, qa=qa, dev=dev, tl=tl,
        test_plan_doc="plan",
        implementation="impl-v1",
        dev_clarification="",
        initial_review="TC-1 severity: blocker",
        product_idea="build X",
        tasks=[],
    )

    # Quota hit before Dev can fix, so impl stays untouched.
    assert out == "impl-v1"
    assert dev.revise_calls == 0


# ── option_c_spec_postmortem tests ───────────────────────────────────────────

def test_option_c_postmortem_returns_false_when_ba_reflection_empty():
    """If BA returns a trivial/empty reflection, postmortem bails."""
    orch = _StubOrch()
    ba   = _StubBA()
    dev  = _StubDev()
    tl   = _StubTL()
    tl._ask_reply = "x"   # too short

    ok = option_c_spec_postmortem(
        orch, tl, ba, dev,
        blockers=["blocker A"], tasks=[],
        implementation="impl", product_idea="idea",
    )

    assert ok is False
    assert ba.revise_specs_calls == 0        # never got to spec rewrite


def test_option_c_postmortem_rewrites_spec_and_reruns_dev():
    """Full rescue path: BA reflection → BA.revise_specs → Dev re-implements."""
    orch = _StubOrch()
    ba   = _StubBA()
    dev  = _StubDev(fix_outputs=["impl-v2-after-spec-refine"])
    tl   = _StubTL()

    ok = option_c_spec_postmortem(
        orch, tl, ba, dev,
        blockers=["blocker A", "blocker B"],
        tasks=[SimpleNamespace(id="TASK-001")],
        implementation="impl-v1", product_idea="idea",
    )

    assert ok is True
    assert ba.revise_specs_calls == 1
    assert dev.revise_calls == 1
    assert orch.results["dev"] == "impl-v2-after-spec-refine"
    # BA checkpoint was persisted with the new spec.
    assert any(k == "ba" for k, _ in orch.saved)


def test_option_c_postmortem_handles_ba_exception():
    """If BA.revise_specs blows up, return False instead of propagating."""
    orch = _StubOrch()
    ba   = _StubBA()
    def _boom(*a, **kw):
        raise RuntimeError("LLM hiccup")
    ba.revise_specs = _boom  # type: ignore[assignment]
    dev  = _StubDev()
    tl   = _StubTL()

    ok = option_c_spec_postmortem(
        orch, tl, ba, dev,
        blockers=["blocker"], tasks=[],
        implementation="impl", product_idea="idea",
    )

    assert ok is False
    assert dev.revise_calls == 0
