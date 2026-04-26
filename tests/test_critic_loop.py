"""
Flow test for pipeline.critic_loop — verifies the revise loop terminates
correctly on PASS, keeps looping on REVISE, and routes to escalate on the
last round. Uses a stub orchestrator so no LLM calls happen.
"""
from types import SimpleNamespace

import pytest

from pipeline.critic_loop import run_with_review, review_only


# ── Stubs ────────────────────────────────────────────────────────────────────

class _StubAgent:
    ROLE = "StubAgent"
    def __init__(self, revise_outputs: list[str]):
        self._revise_outputs = list(revise_outputs)
        self.revised_count = 0
    def revise(self, output, guide, original):
        self.revised_count += 1
        if self._revise_outputs:
            return self._revise_outputs.pop(0)
        return output + " (revised)"


class _StubCritic:
    MAX_ROUNDS = 2
    def __init__(self, verdicts: list[str], scores: list[int]):
        self._verdicts = list(verdicts)
        self._scores = list(scores)
        self.evaluate_count = 0
    def evaluate(self, role, output, agent_key, original_context):
        self.evaluate_count += 1
        return {
            "score": self._scores.pop(0) if self._scores else 7,
            "verdict": self._verdicts.pop(0) if self._verdicts else "PASS",
            "weaknesses": [],
            "revision_guide": ["fix this"],
            "pass_threshold": 7,
        }
    def print_review(self, *a, **kw):
        pass


class _StubOrch:
    def __init__(self, critic):
        self.critic = critic
        self.critic_reviews = []
        self.results = {}
    def _save(self, key, content):
        self.results[key] = content
    def _apply_dynamic_weights(self, review, agent):
        return review
    def _critic_enabled_for(self, key, output="", context=None):
        return True
    def _fast_track_announce(self, *a, **kw):
        pass
    def _record_critic_skip(self, *a, **kw):
        pass
    def _techlead_touches_core(self, out):
        return False, []
    def _maybe_refresh_context(self):
        pass
    def _detect_skill_for(self, agent, task):
        pass


# ── Tests ────────────────────────────────────────────────────────────────────

def test_run_with_review_passes_on_first_round():
    critic = _StubCritic(verdicts=["PASS"], scores=[8])
    orch = _StubOrch(critic)
    agent = _StubAgent(revise_outputs=[])

    out = run_with_review(orch, "dev", agent, produce_fn=lambda: "v1",
                           original_prompt="idea")

    assert out == "v1"
    assert critic.evaluate_count == 1
    assert agent.revised_count == 0
    assert len(orch.critic_reviews) == 1
    assert orch.critic_reviews[0]["verdict"] == "PASS"


def test_run_with_review_revises_then_passes():
    critic = _StubCritic(verdicts=["REVISE", "PASS"], scores=[5, 8])
    orch = _StubOrch(critic)
    agent = _StubAgent(revise_outputs=["v2"])

    out = run_with_review(orch, "dev", agent, produce_fn=lambda: "v1",
                           original_prompt="idea")

    assert out == "v2"
    assert critic.evaluate_count == 2
    assert agent.revised_count == 1
    assert len(orch.critic_reviews) == 2


def test_critic_gate_false_skips_loop_entirely():
    critic = _StubCritic(verdicts=[], scores=[])
    orch = _StubOrch(critic)
    orch._critic_enabled_for = lambda *a, **kw: False  # force skip
    agent = _StubAgent(revise_outputs=[])

    out = run_with_review(orch, "pm", agent, produce_fn=lambda: "original",
                           original_prompt="idea")

    assert out == "original"
    assert critic.evaluate_count == 0
    assert len(orch.critic_reviews) == 0


def test_review_only_no_produce():
    """`review_only` skips the produce step — good for TechLead's pre-built output."""
    critic = _StubCritic(verdicts=["PASS"], scores=[8])
    orch = _StubOrch(critic)
    agent = _StubAgent(revise_outputs=[])

    out = review_only(orch, "techlead", agent, "pre-built output",
                      original_prompt="tasks", context=None)

    assert out == "pre-built output"
    assert critic.evaluate_count == 1
