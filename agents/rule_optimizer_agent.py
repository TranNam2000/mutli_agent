"""Rule Optimizer Agent - Suggests improvements to agent rules and criteria based on critic feedback."""
from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from core.logging import tprint
from .base_agent import BaseAgent, _RULES_DIR


def _load_rule(agent_key: str, target_type: str, profile: str) -> tuple[str, Path | None]:
    """Load current rule/criteria content + path."""
    if target_type == "criteria":
        search = [_RULES_DIR / p / "criteria" / f"{agent_key}.md" for p in [profile, "default"]]
    else:
        search = [_RULES_DIR / p / f"{agent_key}.md" for p in [profile, "default"]]
    for path in search:
        if path.exists():
            return path.read_text(encoding="utf-8").strip(), path
    return "", None


class RuleOptimizerAgent(BaseAgent):
    ROLE = "Rule Optimizer"
    RULE_KEY = "rule_optimizer"

    def analyze_and_suggest(
        self,
        critic_reviews: list[dict],
        chronic_patterns: list[dict] | None = None,
        history=None,  # ReviseHistory instance
        easy_items: list[dict] | None = None,
    ) -> list[dict]:
        if not critic_reviews and not chronic_patterns and not easy_items:
            return []

        profile = getattr(self, "profile", "default")

        # ── Build full context for each affected agent ────────────────────────
        affected_agents = set()
        for r in critic_reviews:
            affected_agents.add(r["agent_key"])
        if chronic_patterns:
            for p in chronic_patterns:
                affected_agents.add(p["agent_key"])
        if easy_items:
            for ei in easy_items:
                affected_agents.add(ei["agent_key"])

        rule_context = self._build_rule_context(affected_agents, profile, history)
        reviews_text = self._format_reviews(critic_reviews) if critic_reviews else "(none REVISE session này)"
        chronic_block = self._format_chronic(chronic_patterns)
        easy_block    = self._format_easy_items(easy_items)

        prompt = f"""=== CRITIC FEEDBACK SESSION NÀY ===
{reviews_text}

{chronic_block}

{easy_block}

=== CONTEXT ĐẦY ĐỦ TỪNG AGENT ===
{rule_context}

Đề xuất cải thiện dựa trên context thực tế ở trên.
With mỗi đề xuất: đọc rule hiện tại → tự kiểm tra CONFLICT_CHECK → chỉ viết ADDITION if SAFE."""

        raw = self._call(self.system_prompt, prompt)
        return self._parse_suggestions(raw, profile)

    # ── Integrity-driven suggestions (deterministic, no LLM tokens) ──────────

    def suggest_from_integrity(self, integrity) -> list[dict]:
        """Translate IntegrityRules state → rule-file ADD suggestions.

        Returns the same shape as `_parse_suggestions` so the orchestrator's
        existing apply-loop can consume them transparently. This is a pure
        function of the on-disk integrity tables — zero tokens are spent.
        """
        if integrity is None:
            return []
        profile = getattr(self, "profile", "default")
        suggestions: list[dict] = []

        try:
            from learning.integrity_rules import (
                MODULE_BLACKLIST_THRESHOLD, REPUTATION_FN_THRESHOLD,
            )
        except (ImportError, ValueError, KeyError, AttributeError):
            MODULE_BLACKLIST_THRESHOLD = 3
            REPUTATION_FN_THRESHOLD = 2

        # 1) Module blacklist → BA criteria clause.
        for module, count in (integrity.module_blacklist or {}).items():
            if count < MODULE_BLACKLIST_THRESHOLD:
                continue
            reason = (f"Integrity: module `{module}` recorded {count} "
                      f"post-skip failures — force Critic when touched.")
            addition = (
                f"- Tasks whose `impact_area` or module == `{module}` MUST "
                f"set `risk_level=high` in metadata and MUST NOT list "
                f"`BA`, `TechLead`, or `PM` in `flow_control.skip_critic`. "
                f"({count} prior false-negatives on record.)"
            )
            suggestions.append(self._build_integrity_suggestion(
                agent_key="ba", target_type="criteria",
                reason=reason, addition=addition, profile=profile,
            ))

        # 2) Keyword risk table → BA criteria clause.
        if integrity.keyword_risk:
            high_kws = sorted(k for k, v in integrity.keyword_risk.items() if v == "high")
            med_kws  = sorted(k for k, v in integrity.keyword_risk.items() if v == "med")
            if high_kws:
                reason = (f"Integrity: {len(high_kws)} keyword(s) promoted "
                          f"to high-risk via failure history.")
                lines = ["- When a task description contains any of these "
                         "keywords, auto-set `risk_level=high`:"]
                lines += [f"  - `{kw}`" for kw in high_kws]
                suggestions.append(self._build_integrity_suggestion(
                    agent_key="ba", target_type="criteria",
                    reason=reason, addition="\n".join(lines), profile=profile,
                ))
            if med_kws:
                reason = (f"Integrity: {len(med_kws)} keyword(s) promoted "
                          f"to med-risk via failure history.")
                lines = ["- Bump `risk_level` at least to `med` when the "
                         "task mentions any of:"]
                lines += [f"  - `{kw}`" for kw in med_kws]
                suggestions.append(self._build_integrity_suggestion(
                    agent_key="ba", target_type="criteria",
                    reason=reason, addition="\n".join(lines), profile=profile,
                ))

        # 3) Agent reputation → reminder clause on that role's rule file.
        role_to_key = {
            "BA": "ba", "TechLead": "techlead", "PM": "pm",
            "Design": "design", "Dev": "dev", "QA": "test",
        }
        for role, rep in (integrity.agent_reputation or {}).items():
            fn = int(rep.get("false_negatives", 0))
            if fn < REPUTATION_FN_THRESHOLD:
                continue
            key = role_to_key.get(role)
            if key is None:
                continue
            reason = (f"Integrity: {role} has {fn} false-negatives from "
                      f"Fast-Track skip decisions.")
            addition = (
                f"- Reputation note: {fn} prior outputs from this role "
                f"passed Fast-Track (no Critic) and then failed QA. Before "
                f"allowing `flow_control.skip_critic` to include `{role}` "
                f"again, you MUST justify in the task description why this "
                f"specific task avoids the prior failure mode."
            )
            suggestions.append(self._build_integrity_suggestion(
                agent_key=key, target_type="rule",
                reason=reason, addition=addition, profile=profile,
            ))

        return suggestions

    def _build_integrity_suggestion(self, *, agent_key: str, target_type: str,
                                      reason: str, addition: str,
                                      profile: str) -> dict:
        """Assemble a suggestion dict in the exact shape orchestrator expects."""
        current, found_path = _load_rule(agent_key, target_type, profile)
        if target_type == "criteria":
            target_path = _RULES_DIR / profile / "criteria" / f"{agent_key}.md"
        else:
            target_path = _RULES_DIR / profile / f"{agent_key}.md"
        if not current:
            current = ""
        suggested = current + ("\n\n" if current else "") + addition
        return {
            "agent_key":      agent_key,
            "target_type":    target_type,
            "action":         "ADD",
            "replace_section": None,
            "profile":        profile,
            "reason":         reason,
            "addition":       addition,
            "current_rule":   current,
            "suggested_rule": suggested,
            "rule_path":      target_path,
            "source":         "integrity",
        }

    def _build_rule_context(self, agent_keys: set, profile: str, history) -> str:
        """Build rich context block: current rule + pass patterns + apply history."""
        parts = []
        for key in sorted(agent_keys):
            lines = [f"### [{key.upper()}]"]

            # Current rule file
            rule_content, _ = _load_rule(key, "rule", profile)
            if rule_content:
                lines.append(f"**Rule hiện tại:**\n{rule_content}")

            # Current criteria file
            crit_content, _ = _load_rule(key, "criteria", profile)
            if crit_content:
                lines.append(f"**Criteria hiện tại:**\n{crit_content}")

            # PASS patterns (protect these)
            if history:
                pass_patterns = history.get_pass_patterns(key)
                if pass_patterns:
                    pp_lines = "\n".join(f"  • {p['sample'][:80]} ({p['count']}x PASS)" for p in pass_patterns[:5])
                    lines.append(f"**PASS patterns (KHÔNG PHÁ VỠ):**\n{pp_lines}")

                # Previously applied suggestions (don't repeat)
                applied = [
                    e for e in history.get_applied_entries()
                    if e["agent_key"] == key
                ]
                if applied:
                    ap_lines = "\n".join(f"  • {e['reason_sample'][:80]}" for e in applied[:3])
                    lines.append(f"**Done apply before đó (KHÔNG lặp lại):**\n{ap_lines}")

            parts.append("\n".join(lines))

        return "\n\n".join(parts)

    def _format_reviews(self, reviews: list[dict]) -> str:
        lines = []
        for r in reviews:
            lines.append(f"=== {r['agent_role']} (Round {r['round']}) ===")
            lines.append(f"Score: {r['score']}/10  Verdict: {r['verdict']}")
            if r.get("score_completeness") is not None:
                lines.append(
                    f"  Completeness={r.get('score_completeness','?')}  "
                    f"Format={r.get('score_format','?')}  "
                    f"Quality={r.get('score_quality','?')}"
                )
            if r.get("weaknesses"):
                lines.append("Weaknesses:")
                for w in r["weaknesses"]:
                    lines.append(f"  - {w}")
            if r.get("revision_guide"):
                lines.append("Revision guide:")
                for g in r["revision_guide"]:
                    lines.append(f"  - {g}")
            lines.append("")
        return "\n".join(lines)

    def _format_easy_items(self, easy_items: list[dict] | None) -> str:
        if not easy_items:
            return ""
        lines = ["=== CHECKLIST ITEMS QUÁ DỄ (YES 100% — need siết chặt hơn) ==="]
        for ei in easy_items:
            lines.append(f"• [{ei['agent_key'].upper()} / criteria] ({ei['total_count']}x YES) {ei['sample']}")
        lines.append("→ Đề xuất ACTION=REPLACE để viết lại item cụ thể và khó hơn.")
        return "\n".join(lines)

    def _format_chronic(self, chronic_patterns: list[dict] | None) -> str:
        if not chronic_patterns:
            return ""
        lines = ["=== LỖI LẶP LẠI NHIỀU SESSION ==="]
        for p in chronic_patterns:
            lines.append(
                f"• [{p['agent_key'].upper()} / {p['target_type']}] "
                f"({p['count']}x) {p['reason_sample']}\n"
                f"  Gợi ý before: {p['addition_sample'][:100]}"
            )
        return "\n".join(lines)

    def _parse_suggestions(self, raw: str, profile: str) -> list[dict]:
        suggestions = []
        blocks = raw.split("<<<END>>>")

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            agent_key = reason = addition = target_type = action = replace_section = conflict_check = None
            addition_lines = []
            in_addition = False

            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith("AGENT:"):
                    agent_key = stripped.split(":", 1)[1].strip().lower()
                    in_addition = False
                elif stripped.startswith("TARGET:"):
                    target_type = stripped.split(":", 1)[1].strip().lower()
                    in_addition = False
                elif stripped.startswith("REASON:"):
                    reason = stripped.split(":", 1)[1].strip()
                    in_addition = False
                elif stripped.startswith("ACTION:"):
                    action = stripped.split(":", 1)[1].strip().upper()
                    in_addition = False
                elif stripped.startswith("REPLACE_SECTION:"):
                    replace_section = stripped.split(":", 1)[1].strip()
                    in_addition = False
                elif stripped.startswith("CONFLICT_CHECK:"):
                    conflict_check = stripped.split(":", 1)[1].strip().upper()
                    in_addition = False
                elif stripped.startswith("ADDITION:"):
                    first = stripped.split(":", 1)[1].strip()
                    if first:
                        addition_lines.append(first)
                    in_addition = True
                elif in_addition and stripped:
                    addition_lines.append(stripped)

            # Skip if LLM self-flagged conflict
            if conflict_check == "CONFLICT":
                continue

            if not (agent_key and reason and addition_lines):
                continue

            addition    = "\n".join(addition_lines[:6])
            action      = action if action in ("ADD", "REPLACE") else "ADD"
            target_type = target_type if target_type in ("rule", "criteria") else "rule"

            current, found_path = _load_rule(agent_key, target_type, profile)
            if not found_path:
                continue

            # Write to active profile dir
            if target_type == "criteria":
                target_path = _RULES_DIR / profile / "criteria" / f"{agent_key}.md"
            else:
                target_path = _RULES_DIR / profile / f"{agent_key}.md"

            if action == "REPLACE" and replace_section:
                suggested_rule = self._replace_section(current, replace_section, addition)
            else:
                suggested_rule = current + "\n\n" + addition

            suggestions.append({
                "agent_key":      agent_key,
                "target_type":    target_type,
                "action":         action,
                "replace_section": replace_section,
                "profile":        profile,
                "reason":         reason,
                "addition":       addition,
                "current_rule":   current,
                "suggested_rule": suggested_rule,
                "rule_path":      target_path,
            })

        return suggestions[:3]

    @staticmethod
    def _replace_section(current: str, section_title: str, new_content: str) -> str:
        lines = current.splitlines()
        title_lower = section_title.lower()
        start = next(
            (i for i, l in enumerate(lines) if l.lstrip("#").strip().lower() == title_lower),
            None,
        )
        if start is None:
            return current + "\n\n" + new_content
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("#")),
            len(lines),
        )
        before = "\n".join(lines[:start])
        after  = "\n".join(lines[end:])
        return (before + "\n" + new_content + ("\n" + after if after.strip() else "")).strip()

    def apply(self, suggestion: dict) -> str:
        """Write improved rule to file, backing up original. Returns backup path."""
        rule_path: Path = suggestion["rule_path"]
        rule_path.parent.mkdir(parents=True, exist_ok=True)

        backup_dir = _RULES_DIR / "backups"
        backup_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_label = "criteria_" if suggestion["target_type"] == "criteria" else ""
        backup_path  = str(backup_dir / f"{target_label}{rule_path.stem}_{ts}.md")

        if rule_path.exists():
            shutil.copy(rule_path, backup_path)

        rule_path.write_text(suggestion["suggested_rule"], encoding="utf-8")
        return backup_path

    def rollback(self, backup_path: str, rule_path: Path) -> bool:
        src = Path(backup_path)
        if not src.exists():
            tprint(f"  ❌ Backup không tồn tại: {backup_path}")
            return False
        shutil.copy(src, rule_path)
        return True

    def print_suggestion(self, s: dict):
        icon  = "📋" if s["target_type"] == "criteria" else "📜"
        label = "CRITERIA" if s["target_type"] == "criteria" else "RULE"
        action_label = f"[{s.get('action','ADD')}]"
        tprint(f"\n  {'─'*60}")
        tprint(f"  🧠 {label} {action_label} — {s['agent_key'].upper()}  {icon}")
        tprint(f"  {'─'*60}")
        tprint(f"  File   : rules/{s['profile']}/{'criteria/' if s['target_type'] == 'criteria' else ''}{s['agent_key']}.md")
        tprint(f"  Reason : {s['reason']}")
        tprint(f"  Thêm:")
        for line in s["addition"].splitlines():
            tprint(f"    + {line}")
        tprint(f"  {'─'*60}")
