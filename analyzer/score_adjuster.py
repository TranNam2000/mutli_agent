"""
Score Adjuster — makes critic scores reflect real-world outcomes.

Pure critic score = "did the output satisfy the checklist" — can be gamed.
Adjusted score blends in:
  1. Test outcomes (Patrol/Maestro): Dev/Design scores penalized when tests fail
  2. Downstream clarifications: BA/TechLead penalized when next agent asked many questions
  3. MISSING_INFO leakage: upstream penalized when downstream has unresolved missing info
  4. Cost overage: all agents penalized when token usage wildly exceeds scope expectation

Dynamic weights: simple / feature / full_app have different dimension weights.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


# Scope-specific dimension weights (completeness, format, quality)
# Each tuple sums to 1.0
_SCOPE_WEIGHTS = {
    "simple":   (0.30, 0.30, 0.40),  # format matters more for small output
    "bug_fix":  (0.25, 0.15, 0.60),  # quality (correctness) dominates
    "feature":  (0.35, 0.20, 0.45),  # balanced
    "module":   (0.40, 0.20, 0.40),  # coverage important
    "full_app": (0.45, 0.15, 0.40),  # completeness dominates for large scope
}

# Expected token budgets per scope (for cost-aware scoring)
_SCOPE_TOKEN_BUDGET = {
    "simple":   15_000,
    "bug_fix":  20_000,
    "feature":  60_000,
    "module":   120_000,
    "full_app": 300_000,
}


@dataclass
class ScoreAdjustment:
    original:   int
    adjusted:   int
    penalties:  list[tuple[str, float]] = field(default_factory=list)  # [(reason, -delta)]
    bonuses:    list[tuple[str, float]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{self.original}/10 → {self.adjusted}/10"]
        for reason, d in self.penalties:
            lines.append(f"  − {d:.1f}  {reason}")
        for reason, d in self.bonuses:
            lines.append(f"  + {d:.1f}  {reason}")
        return "\n".join(lines)


class ScoreAdjuster:
    def __init__(self):
        self.adjustments: list[dict] = []

    # ── Dynamic weights ───────────────────────────────────────────────────────

    def recompute_with_scope(self, review: dict, scope: str) -> dict:
        """Re-weight existing dimension scores using scope-specific weights."""
        weights = _SCOPE_WEIGHTS.get(scope, (0.40, 0.20, 0.40))
        c = review.get("score_completeness", 5)
        f = review.get("score_format", 5)
        q = review.get("score_quality", 5)
        new_final = int(round(c * weights[0] + f * weights[1] + q * weights[2]))
        new_final = max(1, min(10, new_final))

        if new_final != review.get("score"):
            review["score_before_scope_reweight"] = review.get("score")
            review["score"] = new_final
            review["scope_applied"] = scope
        return review

    # ── Test-informed (Patrol + Maestro) ──────────────────────────────────────

    def apply_test_outcomes(self, reviews: list[dict], patrol_result=None,
                            maestro_result=None,
                            missing_info_attribution: dict | None = None) -> list[dict]:
        """
        Blend test outcomes into scores using a **weighted sum** (not compound
        subtraction) so no single axis can crash a score to 1. Dev's test-
        failure penalty is re-attributed upstream when `missing_info_attribution`
        shows the failures trace back to BA / TechLead missing info.

        Parameters
        ----------
        missing_info_attribution : dict[agent_key] -> int
            Count of MISSING_INFO items in Dev's output that point at each
            upstream agent. If supplied, ``share = source_count / total``
            of the Dev test-failure penalty is moved to that source agent.
        """
        if not patrol_result and not maestro_result:
            return reviews

        patrol_rate = self._patrol_pass_rate(patrol_result)
        maestro_rate = self._maestro_pass_rate(maestro_result)

        # Compute raw fail penalty (0–1 scale), then blend at the end.
        patrol_fail  = (1.0 - patrol_rate) if patrol_rate  is not None else 0.0
        maestro_fail = (1.0 - maestro_rate) if maestro_rate is not None else 0.0
        # Total test-outcome pressure on Dev, capped at 1.0
        dev_pressure = min(1.0, 0.7 * patrol_fail + 0.6 * maestro_fail)

        # Attribution: if Dev's output has MISSING_INFO pointing at BA/TL,
        # move a share of the penalty upstream.
        total_missing = sum((missing_info_attribution or {}).values())
        upstream_share: dict[str, float] = {}
        if total_missing and dev_pressure > 0:
            # Dev still keeps some share (at least 30%) — never fully exonerated.
            dev_keep = 0.3
            movable  = dev_pressure * (1 - dev_keep)
            for src, cnt in (missing_info_attribution or {}).items():
                if src == "dev" or not cnt:
                    continue
                upstream_share[src] = movable * (cnt / total_missing)
            dev_pressure *= dev_keep + (1 - dev_keep) * (1 - (total_missing and 1 or 0))
            # When any missing_info exists, Dev share drops to dev_keep*original.
            dev_pressure = dev_pressure if not upstream_share else dev_pressure * dev_keep / dev_keep

        # Weighted-sum score: final = 0.7*raw + 0.3*(10 * (1 - pressure))
        # Pressure=0 → full credit; pressure=1 → bottom weight reaches 0/10 on that axis.
        def _blend(raw: int, pressure: float) -> int:
            if pressure <= 0:
                return raw
            outcome_pts = 10 * (1.0 - pressure)
            blended = 0.7 * raw + 0.3 * outcome_pts
            return max(1, int(round(blended)))

        for r in reviews:
            if r["agent_key"] != "dev":
                continue
            original = r["score"]
            new_score = _blend(original, dev_pressure)
            if new_score != original:
                adj = ScoreAdjustment(original=original, adjusted=new_score)
                delta = original - new_score
                if patrol_fail:
                    adj.penalties.append(
                        (f"Patrol tests fail {patrol_fail*100:.0f}% (blended)",
                         delta * (0.7 * patrol_fail / max(0.001, 0.7 * patrol_fail + 0.6 * maestro_fail))),
                    )
                if maestro_fail:
                    adj.penalties.append(
                        (f"Maestro fail {maestro_fail*100:.0f}% (blended)",
                         delta * (0.6 * maestro_fail / max(0.001, 0.7 * patrol_fail + 0.6 * maestro_fail))),
                    )
                r["score_original"] = original
                r["score"] = new_score
                r["score_adjustment"] = adj.summary()
                self.adjustments.append({
                    "agent_key": "dev", "kind": "test_outcome",
                    "detail": adj.summary(),
                })

        # Upstream attribution: shift part of dev pressure onto BA / TechLead
        # that Dev called out via MISSING_INFO.
        for r in reviews:
            key = r.get("agent_key")
            share = upstream_share.get(key, 0.0)
            if share <= 0:
                continue
            original = r["score"]
            new_score = _blend(original, share)
            if new_score != original:
                r.setdefault("score_original", original)
                r["score"] = new_score
                prev = r.get("score_adjustment", "")
                note = f"{original} → {new_score}/10\n  − {(original-new_score):.1f}  Dev MISSING_INFO traced to you ({share:.0%} of test failure)"
                r["score_adjustment"] = (prev + "\n" + note).strip()
                self.adjustments.append({
                    "agent_key": key, "kind": "upstream_attribution",
                    "detail": note,
                })

        # Design responds to Maestro visual diff — also blended.
        if maestro_rate is not None and maestro_rate < 1.0:
            design_pressure = min(1.0, 0.35 * maestro_fail)   # lighter than Dev
            for r in reviews:
                if r["agent_key"] != "design":
                    continue
                original = r["score"]
                new_score = _blend(original, design_pressure)
                if new_score != original:
                    r["score_original"] = original
                    r["score"] = new_score
                    r["score_adjustment"] = f"Design {original} → {new_score}/10 (Maestro visual diff, blended)"
                    self.adjustments.append({
                        "agent_key": "design", "kind": "visual_diff",
                        "detail": r["score_adjustment"],
                    })

        return reviews

    # ── Downstream-informed (clarification count, MISSING_INFO leakage) ──────

    def apply_downstream_signals(self, reviews: list[dict],
                                  downstream_signals: dict) -> list[dict]:
        """
        downstream_signals: {
          "ba":       {"clarif_count": int, "missing_info_downstream": int},
          "techlead": {"clarif_count": int, ...},
          ...
        }
        clarif_count = number of times the next agent asked this agent for clarification
        missing_info_downstream = number of MISSING_INFO in dev output referring to this agent
        """
        for r in reviews:
            agent_key = r["agent_key"]
            sig = downstream_signals.get(agent_key, {})
            clarif = sig.get("clarif_count", 0)
            missing = sig.get("missing_info_downstream", 0)
            if clarif == 0 and missing == 0:
                continue

            adj = ScoreAdjustment(original=r["score"], adjusted=r["score"])
            # Each clarification beyond the 1st = -0.5, capped
            if clarif > 1:
                delta = min((clarif - 1) * 0.5, 3.0)
                adj.adjusted = max(1, int(round(adj.adjusted - delta)))
                adj.penalties.append(
                    (f"Downstream asked {clarif} clarifications", delta)
                )
            # Each unresolved MISSING_INFO in downstream = -1
            if missing > 0:
                delta = min(missing * 1.0, 4.0)
                adj.adjusted = max(1, int(round(adj.adjusted - delta)))
                adj.penalties.append(
                    (f"{missing} MISSING_INFO leaked downstream", delta)
                )
            if adj.adjusted != adj.original:
                r.setdefault("score_original", adj.original)
                r["score"] = adj.adjusted
                prev = r.get("score_adjustment", "")
                r["score_adjustment"] = (prev + "\n" + adj.summary()).strip()
                self.adjustments.append({
                    "agent_key": agent_key, "kind": "downstream",
                    "detail": adj.summary(),
                })
        return reviews

    # ── Cost-aware ────────────────────────────────────────────────────────────

    def apply_cost_penalty(self, reviews: list[dict], tokens_by_agent: dict,
                           scope: str) -> list[dict]:
        """Penalize agents that consumed way more tokens than expected for scope."""
        budget = _SCOPE_TOKEN_BUDGET.get(scope, 60_000)
        # Per-agent budget ≈ total / 5
        per_agent_budget = budget / 5

        for r in reviews:
            used = tokens_by_agent.get(r.get("agent_role", ""), 0)
            if used <= per_agent_budget * 1.5:
                continue  # within 50% over → no penalty
            overage_ratio = (used - per_agent_budget) / per_agent_budget
            delta = min(overage_ratio * 0.5, 2.0)  # cap -2
            original = r["score"]
            new_score = max(1, int(round(original - delta)))
            if new_score != original:
                r.setdefault("score_original", original)
                r["score"] = new_score
                prev = r.get("score_adjustment", "")
                r["score_adjustment"] = (
                    prev + f"\n  − {delta:.1f}  Over budget ({used:,} vs {int(per_agent_budget):,})"
                ).strip()
                self.adjustments.append({
                    "agent_key": r["agent_key"], "kind": "cost_overage",
                    "detail": f"{used:,} tokens vs budget {int(per_agent_budget):,}",
                })
        return reviews

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _patrol_pass_rate(result) -> float | None:
        if not result:
            return None
        rates = []
        for r in [getattr(result, "android", None), getattr(result, "ios", None)]:
            if r:
                total = r.passed + r.failed
                if total > 0:
                    rates.append(r.passed / total)
        if not rates:
            return None
        return sum(rates) / len(rates)

    @staticmethod
    def _maestro_pass_rate(result) -> float | None:
        if not result or not getattr(result, "flows", None):
            return None
        flows = result.flows
        if not flows:
            return None
        passed = sum(1 for f in flows if f.passed)
        return passed / len(flows)


def count_clarifications_from_bus(message_bus, asker_role: str, target_role: str) -> int:
    """Count how many times asker asked target via the message bus."""
    if not message_bus:
        return 0
    return sum(
        1 for msg in message_bus.log
        if msg.from_agent == asker_role and msg.to_agent == target_role
    )


def count_missing_info(text: str) -> dict[str, int]:
    """Parse MISSING_INFO blocks and return {source_agent: count}."""
    counts: dict[str, int] = {}
    for m in re.finditer(r"MISSING_INFO:.*?MUST_ASK:\s*(\w+)", text, re.IGNORECASE):
        src = m.group(1).strip().lower()
        norm = {"ba": "ba", "techlead": "techlead", "tech": "techlead",
                "design": "design", "pm": "pm", "user": "user"}.get(src, src)
        counts[norm] = counts.get(norm, 0) + 1
    return counts
