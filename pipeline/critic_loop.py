"""
Critic + revise loop — extracted from ProductDevelopmentOrchestrator.

These functions take the orchestrator instance explicitly (`orch`) so they
can be tested without running a full pipeline and to make the dependency
relationship obvious. The original methods on orchestrator now delegate here.

Public functions
----------------
    run_with_review(orch, key, agent, produce_fn, original_prompt) -> str
    review_only   (orch, key, agent, output, original_prompt, context) -> str
    escalate      (orch, key, agent, output, review, original_prompt) -> str

Shared invariants
-----------------
  * Each revise round appends a new entry to ``orch.critic_reviews``.
  * The loop respects ``orch.critic.MAX_ROUNDS``.
  * Failed-gate (`_critic_enabled_for` returns False) → fast-track, no review.
  * Every output written with `orch._save()` + cached in `orch.results[key]`.
"""
from __future__ import annotations

from core.logging import tprint


def _critic_loop(orch, key, agent, output: str, original_prompt: str) -> str:
    """
    Shared body used by both `run_with_review` and `review_only`.
    Runs up to `MAX_ROUNDS` Critic passes, revises between, and escalates
    to the user on the last failed round.
    """
    for round_num in range(1, orch.critic.MAX_ROUNDS + 1):
        tprint(f"  🔍 Critic reviewing [{key}] round {round_num}...")
        review = orch.critic.evaluate(
            agent.ROLE, output,
            agent_key=key, original_context=original_prompt,
        )
        review = orch._apply_dynamic_weights(review, agent)
        threshold = review.get("pass_threshold", 7)
        review["verdict"] = "PASS" if review["score"] >= threshold else "REVISE"
        orch.critic.print_review(agent.ROLE, review, round_num)
        orch.critic_reviews.append({
            **review,
            "agent_key":  key,
            "agent_role": agent.ROLE,
            "round":      round_num,
        })
        if review["verdict"] == "PASS":
            return output
        if round_num < orch.critic.MAX_ROUNDS:
            tprint(f"\n  🔄 {agent.ROLE} is improving its output...")
            output = agent.revise(output, review["revision_guide"], original_prompt)
            orch._save(key, output)
            orch.results[key] = output
        else:
            output = escalate(orch, key, agent, output, review, original_prompt)
    return output


def run_with_review(orch, key: str, agent, produce_fn,
                     original_prompt: str = "") -> str:
    """Produce output, save checkpoint, then run the Critic + revise loop.

    If the gate (`_critic_enabled_for`) says skip, returns output untouched.
    """
    orch._maybe_refresh_context()
    orch._detect_skill_for(agent, original_prompt)
    output = produce_fn()
    orch._save(key, output)
    orch.results[key] = output

    if not orch._critic_enabled_for(key, output):
        orch._fast_track_announce(key, {})
        return output

    if key == "techlead":
        _, matches = orch._techlead_touches_core(output)
        tprint(f"  🏛️  TechLead touched core files {matches[:3]} → running Critic")

    return _critic_loop(orch, key, agent, output, original_prompt)


def review_only(orch, key: str, agent, output: str,
                 original_prompt: str = "", context: dict | None = None) -> str:
    """Run Critic against an already-produced output (no produce_fn).

    Used for agents (e.g. TechLead) whose output is assembled from multiple
    internal calls — we don't want to re-run production, only score the
    final artefact.
    """
    if not orch._critic_enabled_for(key, output, context):
        orch._fast_track_announce(key, context or {})
        orch._record_critic_skip(key, (context or {}).get("tasks") or [])
        return output

    if key == "techlead":
        _, matches = orch._techlead_touches_core(output)
        tprint(f"  🏛️  TechLead touched core files {matches[:3]} → running Critic")

    return _critic_loop(orch, key, agent, output, original_prompt)


def escalate(orch, key: str, agent, output: str, review: dict,
              original_prompt: str) -> str:
    """After MAX_ROUNDS still REVISE — ask the user what to do."""
    tprint(f"\n  {'═'*60}")
    tprint(f"  🚨 ESCALATION — {agent.ROLE} [{key}] score {review['score']}/10 "
           f"after {orch.critic.MAX_ROUNDS} rounds")
    tprint(f"  {'═'*60}")
    if review.get("weaknesses"):
        tprint("  Remaining issues:")
        for w in review["weaknesses"][:3]:
            tprint(f"    • {w}")
    tprint(f"\n  Options:")
    tprint(f"    [C] Continue with current output (score {review['score']}/10)")
    tprint(f"    [R] Retry one more round")
    tprint(f"    [S] Skip this step (warning: downstream agents will lack input)")

    while True:
        try:
            choice = input("  Choose [C/R/S]: ").strip().upper()
        except EOFError:
            choice = "C"
        if choice in ("C", "R", "S", ""):
            break
        tprint("  Enter C, R, or S.")

    if choice == "R":
        tprint(f"\n  🔄 Retrying one more round per user request...")
        output = agent.revise(output, review["revision_guide"], original_prompt)
        orch._save(key, output)
        orch.results[key] = output
        extra_review = orch.critic.evaluate(
            agent.ROLE, output, agent_key=key, original_context=original_prompt,
        )
        orch.critic.print_review(agent.ROLE, extra_review, orch.critic.MAX_ROUNDS + 1)
        orch.critic_reviews.append({
            **extra_review,
            "agent_key":  key,
            "agent_role": agent.ROLE,
            "round":      orch.critic.MAX_ROUNDS + 1,
        })
        tprint(f"  {'─'*60}")
    elif choice == "S":
        tprint(f"\n  ⏭️  Skipping {agent.ROLE} — downstream agents will have no input.")
        output = f"[SKIPPED by user — score {review['score']}/10 "\
                 f"after {orch.critic.MAX_ROUNDS} rounds]"
        orch.results[key] = output
    else:
        tprint(f"\n  ▶  Continuing with current output (score {review['score']}/10).")

    return output
