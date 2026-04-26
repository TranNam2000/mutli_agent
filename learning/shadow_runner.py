"""
Shadow-rule A/B runner — flips agent `_rule_variant` between baseline and
shadow per session (so both collect samples), then logs average score back
to ShadowLog after the session finishes.

Extracted from orchestrator so shadow A/B lives near the other learning
state it coordinates with.

Public API
----------
    activate_rule_variants  (orch) -> None
    log_shadow_rule_scores  (orch) -> None
"""
from __future__ import annotations

from core.config import get_bool
from core.logging import tprint


def activate_rule_variants(orch):
    """At session start, pick baseline vs shadow variant for each agent
    that has a live rule A/B test. Deterministic per session (session_id
    hashed), alternates so baseline and shadow both accumulate samples.

    Gated: until `outcome_logger` has ≥ SHADOW_AB_MIN_TOTAL_SESSIONS
    total entries, we stick to baseline. This avoids serving unvalidated
    shadow variants to users who don't yet have enough data for a trusted
    verdict. Override via env MULTI_AGENT_SHADOW_AB_FORCE=1.
    """
    from learning.rule_evolver import ShadowLog
    from agents.base_agent import _RULES_DIR
    import hashlib

    if not get_bool("MULTI_AGENT_SHADOW_AB_FORCE"):
        try:
            from analyzer import load_outcome_entries
            total = len(load_outcome_entries(orch.profile))
            if total < orch.SHADOW_AB_MIN_TOTAL_SESSIONS:
                # Serve baseline only — explicit note once per session.
                tprint(f"  🧪 Shadow A/B gated off (data {total} < "
                       f"{orch.SHADOW_AB_MIN_TOTAL_SESSIONS}) — serving baseline.")
                return
        except (ValueError, KeyError, AttributeError, IndexError, OSError):
            pass

    shadow_log = ShadowLog(_RULES_DIR / orch.profile)
    variants = shadow_log._data.get("variants", {}) if hasattr(shadow_log, "_data") else {}
    if not variants:
        return
    seed = int(hashlib.md5(orch.session_id.encode("utf-8")).hexdigest()[:8], 16)
    for variant_key, info in variants.items():
        agent_key, target_type = variant_key.split(":", 1)
        if target_type != "rule":
            continue  # only baseline prompt has a shadow variant mechanism
        agent = orch.agents.get(agent_key)
        if agent is None:
            continue
        # Alternate: use run counts so both sides are sampled evenly.
        n_base   = len(info.get("baseline", []))
        n_shadow = len(info.get("shadow",   []))
        if n_shadow < n_base:
            pick = "shadow"
        elif n_base < n_shadow:
            pick = "baseline"
        else:
            pick = "shadow" if (seed & 1) else "baseline"
        agent._rule_variant = pick
        tprint(f"  🧪 [{agent_key}] rule A/B: loading {pick}")


def log_shadow_rule_scores(orch):
    """After session, log average score per agent into ShadowLog for
    whichever variant that agent was running. Prereq for verdicts()
    to decide promote / demote next time."""
    from learning.rule_evolver import ShadowLog
    from agents.base_agent import _RULES_DIR
    if not getattr(orch, "critic_reviews", None):
        return
    shadow_log = ShadowLog(_RULES_DIR / orch.profile)
    variants = shadow_log._data.get("variants", {})
    if not variants:
        return
    scores_by_agent: dict[str, list[float]] = {}
    for r in orch.critic_reviews:
        key = r.get("agent_key", "")
        if key and r.get("score") is not None:
            scores_by_agent.setdefault(key, []).append(float(r["score"]))
    for variant_key, _info in variants.items():
        agent_key, target_type = variant_key.split(":", 1)
        if target_type != "rule":
            continue
        scores = scores_by_agent.get(agent_key, [])
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        agent = orch.agents.get(agent_key)
        variant = getattr(agent, "_rule_variant", "baseline") if agent else "baseline"
        shadow_log.log_run(agent_key, "rule", variant, avg, orch.session_id)
        tprint(f"  📊 Shadow log: [{agent_key}] {variant}={avg:.2f}")
