"""Tech Lead Agent - Architecture decisions and technical guidance."""
from __future__ import annotations
from .base_agent import BaseAgent


# Module keywords used by enrich_metadata() to derive impact_area.
_IMPACT_KEYWORDS = {
    "payment":           ["payment", "checkout", "stripe", "vnpay", "momo", "paypal"],
    "auth":              ["auth", "login", "sso", "oauth", "jwt", "token", "password", "session"],
    "core":              ["main.dart", "app_router", "service_locator",
                          "base class", "dependency_injection", "di container"],
    "state_management":  ["bloc", "cubit", "provider", "riverpod", "redux", "mobx"],
    "api":               ["api", "endpoint", "http", "rest", "graphql", "dio", "retrofit"],
    "database":          ["database", "sqlite", "hive", "isar", "room"],
    "ui":                ["screen", "widget", "animation", "layout", "page"],
    "routing":           ["navigator", "go_router", "deep link", "deeplink", "url"],
    "storage":           ["shared_preferences", "secure storage", "keychain"],
    "network":           ["websocket", "socket", "mqtt", "stream"],
}


class TechLeadAgent(BaseAgent):
    ROLE = "Tech Lead"
    RULE_KEY = "techlead"
    SKILL_KEY = "techlead"

    # ── Metadata enrichment (role contribution) ──────────────────────────────

    def enrich_metadata(self, tasks: list) -> int:
        """
        Populate each task's metadata with architecture-aware fields:
          - technical_debt.impact_area (derived from task text via keyword match)
          - context.risk_level (bumped to 'med' if touching core/payment/auth
            but currently tagged 'low')

        Returns number of tasks whose metadata was updated.
        """
        updated = 0
        for t in tasks:
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
            if m is None:
                continue
            hay = " ".join([
                t.title or "", t.description or "",
                " ".join(t.acceptance_criteria or []),
                t.module or "",
            ]).lower()

            hits: set[str] = set(m.technical_debt.impact_area)
            for area, kws in _IMPACT_KEYWORDS.items():
                if any(kw.lower() in hay for kw in kws):
                    hits.add(area)

            if hits != set(m.technical_debt.impact_area):
                m.technical_debt.impact_area = sorted(hits)
                updated += 1

            if m.context.risk_level == "low" and any(
                a in hits for a in ("core", "payment", "auth")
            ):
                m.context.risk_level = "med"
                updated += 1

        return updated

    # ── Option B: batch-review BA spec proactively ───────────────────────────

    def review_ba_spec_batch(self, ba_agent, tasks: list) -> dict:
        """
        Scan BA output for ambiguous tasks (regex smell test). If any red
        flag is found, ask BA ONCE with a consolidated list of clarifications
        and patch the answer back into the affected tasks' descriptions.

        Returns:
          {"flagged": [...], "question": str, "answer": str}  when BA was asked
          {"flagged": []}                                     when nothing to ask
        """
        flagged: list[dict] = []
        for t in tasks or []:
            flags = self._smell_test_spec(t)
            if flags:
                flagged.append({
                    "id":    t.id,
                    "title": (t.title or "").strip()[:80],
                    "flags": flags,
                })
        if not flagged:
            return {"flagged": []}

        question = self._format_batch_question(flagged)
        try:
            answer = self.ask(ba_agent, question)
        except Exception as e:
            return {"flagged": flagged, "question": question,
                    "answer": f"(ask failed: {e})"}

        # Patch each flagged task with BA's clarifications.
        self._apply_ba_clarifications(tasks, answer)
        return {"flagged": flagged, "question": question, "answer": answer}

    @staticmethod
    def _smell_test_spec(task) -> list[str]:
        """Regex-only red-flag detector. Zero LLM cost."""
        flags: list[str] = []
        desc = (task.description or "").lower()
        ac_n = len(task.acceptance_criteria or [])
        if ac_n < 2:
            flags.append(f"AC chỉ có {ac_n} mục")
        if len(task.description or "") < 80:
            flags.append("description ngắn (<80 chars)")
        if "missing_info" in desc:
            flags.append("còn MISSING_INFO")
        for vague in ("as usual", "should work", "works normally",
                       "standard behavior", "như thường"):
            if vague in desc:
                flags.append(f"phrasing mơ hồ: '{vague}'")
                break
        try:
            m = task.get_metadata() if hasattr(task, "get_metadata") else None
        except Exception:
            m = None
        if m is not None:
            if m.touches_core() and m.context.complexity in ("L", "XL"):
                flags.append("core-area + complexity L/XL")
            if m.context.scope == "hotfix" and ac_n == 0:
                flags.append("hotfix không có AC")
        return flags

    @staticmethod
    def _format_batch_question(flagged: list[dict]) -> str:
        """Build one consolidated question TL → BA."""
        lines = [
            "Tôi là Tech Lead. Trước khi prioritize + giao cho Dev, một số "
            "task sau có spec chưa đủ rõ. Vui lòng bổ sung AC hoặc clarify:",
            "",
        ]
        for item in flagged[:6]:
            lines.append(f"### {item['id']} — {item['title']}")
            lines.append(f"  Red flags: {', '.join(item['flags'])}")
            lines.append("")
        lines += [
            "Trả lời format:",
            "",
            "CLARIFY TASK-XXX:",
            "- AC: GIVEN ... WHEN ... THEN ...",
            "- Edge case: ...",
            "",
            "Nếu task đã đầy đủ, ghi: CLARIFY TASK-XXX: OK",
        ]
        return "\n".join(lines)

    @staticmethod
    def _apply_ba_clarifications(tasks: list, answer: str) -> int:
        """Parse BA answer, append new AC / edge-case lines to matching tasks."""
        import re as _re
        pattern = _re.compile(
            r"CLARIFY\s+(TASK-[\w\-]+)\s*:\s*(.+?)(?=\n\s*CLARIFY\s+TASK-|\Z)",
            _re.DOTALL | _re.IGNORECASE,
        )
        patched = 0
        for m in pattern.finditer(answer):
            tid, body = m.group(1), m.group(2).strip()
            if body.upper().startswith("OK"):
                continue
            task = next((t for t in tasks if t.id == tid), None)
            if task is None:
                continue
            # Append extra AC lines preserved from BA's answer.
            extra_ac: list[str] = []
            for line in body.splitlines():
                s = line.strip().lstrip("-* ").strip()
                if s.lower().startswith("ac:"):
                    s = s[3:].strip()
                if not s:
                    continue
                if s.lower().startswith("edge case:"):
                    s = s[10:].strip()
                    extra_ac.append(f"Edge: {s}")
                else:
                    extra_ac.append(s)
            if extra_ac:
                task.acceptance_criteria = list(task.acceptance_criteria or []) + extra_ac
                # Also append a note in description so downstream sees the patch.
                note = "\n\n[TL clarified via BA]: " + "; ".join(extra_ac[:2])
                task.description = (task.description or "") + note
                patched += 1
        return patched

    # ── Task-based flow ───────────────────────────────────────────────────────

    def prioritize_and_assign(self, tasks: list, resources: dict | None = None) -> dict:
        """
        Input: task list ( classified by BA, with design_ref if UI).
        - Validate BA estimates (adjust complexity/risk if thấy sai)
        - Prioritize per (priority × risk / complexity) + topo sort deps
        - Pack into sprint buckets per team capacity

        Returns {sprint_plan, adjustments, resources, summary_markdown}.
        """
        from learning.task_models import plan_sprints, Resources, format_task_list

        res = Resources(
            dev_slots=     (resources or {}).get("dev_slots",     2),
            sprint_hours=  (resources or {}).get("sprint_hours",  80),
            sprints_ahead= (resources or {}).get("sprints_ahead", 3),
        )
        adjustments = self._review_task_estimates(tasks)
        plan = plan_sprints(tasks, res)

        md = [
            f"# Sprint Plan — {res.dev_slots} devs × {res.sprint_hours}h × {res.sprints_ahead} sprints\n",
            f"\n## Resources\n- Dev slots: {res.dev_slots}\n- Sprint capacity: {res.sprint_hours}h per dev\n- Horizon: {res.sprints_ahead} sprints\n",
        ]
        if adjustments:
            md.append("\n## TechLead Adjustments\n")
            for adj in adjustments:
                md.append(f"- {adj}\n")
        md.append("\n## Top 10 by priority score\n```\n")
        md.append(format_task_list(sorted(tasks, key=lambda t: -t.priority_score)[:10]))
        md.append("\n```\n\n## Sprint Breakdown\n```\n")
        md.append(plan.summary())
        md.append("\n```\n")

        return {
            "sprint_plan": plan,
            "adjustments": adjustments,
            "resources":   res,
            "summary_markdown": "".join(md),
        }

    def _review_task_estimates(self, tasks: list) -> list[str]:
        """Flag suspicious complexity/risk estimates — actually apply fixes."""
        if not tasks:
            return []
        sample = "\n".join(
            f"{t.id} ({t.type.value}, {t.complexity.value}, risk={t.risk.value}): {t.title[:60]}"
            for t in tasks[:20]
        )
        system = (
            "You is Tech Lead review task list from BA. Evaluate complexity/risk "
            "look reasonable. Reply briefly — max 5 adjustments."
        )
        prompt = f"""Tasks from BA:
{sample}

If any task estimate looks wrong (VD: auth flow complexity=S is too low, bug changing color complexity=L is too high), output format:
ADJUST: TASK-XXX complexity S→M — lý do
ADJUST: TASK-XXX risk low→high — lý do

If all OK: NONE"""
        try:
            raw = self._call(system, prompt, max_tokens=500)
        except Exception:
            return []
        if "NONE" in raw.upper():
            return []

        adjustments = []
        import re as _re
        for line in raw.splitlines():
            s = line.strip()
            if not s.startswith("ADJUST:"):
                continue
            adjustments.append(s[7:].strip())
            m = _re.match(
                r"(TASK-\w+)\s+(complexity|risk)\s+(\w+)\s*[→->]+\s*(\w+)",
                s[7:].strip(),
            )
            if m:
                tid, field, _old, new = m.groups()
                for t in tasks:
                    if t.id != tid:
                        continue
                    try:
                        if field == "complexity":
                            from learning.task_models import Complexity
                            t.complexity = Complexity(new.upper())
                        elif field == "risk":
                            from learning.task_models import Risk
                            t.risk = Risk(new.lower())
                    except ValueError:
                        pass
        return adjustments

    def assign_to_dev(self, dev_agent: BaseAgent, tech_specs: str) -> str:
        """TechLead proactively assigns task breakdown to Dev before Dev codes."""
        task_prompt = f"""Based on the Technical Architecture Document below, build a concrete task list for Dev.
Each task: file name, layer (entity/repo/bloc/ui), short description, estimate (hours), dependency.

Architecture (summary):
{tech_specs[:2000]}"""
        task_list = self._call(
            f"You is {self.ROLE}. Build a concise, actionable dev task breakdown — max 200 words.",
            task_prompt,
            max_tokens=600,
        )
        return self.ask(dev_agent, task_list)

    def triage_bugs(self, dev_agent: BaseAgent, blockers: list[str], implementation: str) -> str:
        """QA reports bugs → TechLead analyzes root cause and assigns fix tasks to Dev."""
        bug_list = "\n".join(f"- {b}" for b in blockers)
        triage_prompt = f"""QA just found these BLOCKERs:

{bug_list}

=== IMPLEMENTATION (summary) ===
{implementation[:1500]}

Analyze root cause and build a fix task list for Dev:
ROOT_CAUSE: [architectural issue | implementation bug | missing feature]
FIX_TASKS:
- [specific file/layer] [action needed]
PRIORITY: [fix order]"""
        fix_tasks = self._call(
            f"You is {self.ROLE}. Triage bugs from QA, assign concrete fix tasks to Dev — max 150 words.",
            triage_prompt,
            max_tokens=400,
        )
        return self.ask(dev_agent, fix_tasks)

    def review_implementation(self, implementation: str, tech_specs: str) -> str:
        """TechLead reviews Dev's code — returns feedback before handing off to QA."""
        prompt = f"""Review the implementation below from a Tech Lead perspective.

=== TECHNICAL SPECS (target standard) ===
{tech_specs[:1500]}

=== IMPLEMENTATION ===
{implementation[:3000]}

Answer concisely (max 200 words):
APPROVED: [YES | NO — if NO must fix before QA]
ISSUES:
- [specific issue if any: wrong layer, wrong pattern, security hole, missing error handling]
FEEDBACK_FOR_DEV:
- [concrete fix guidance if needed]"""
        return self._call(self.system_prompt, prompt, max_tokens=600)

    def architect_with_context(
        self,
        prd: str,
        project_plan: str,
        design_specs: str,
        pm_feedback: str,
    ) -> str:
        prompt = f"""Based on the documents below, build the Technical Architecture Document:

=== PRD (summary) ===
{prd[:1500]}...

=== PROJECT PLAN (summary) ===
{project_plan[:1000]}...

=== DESIGN SPECS (summary) ===
{design_specs[:1000]}...

=== PM FEEDBACK ON TIMELINE ===
{pm_feedback}

Build the full technical architecture. If info is missing to decide, note explicitly:
MISSING_INFO: [info needed] — MUST_ASK: [BA | PM | User]"""
        return self._call(self.system_prompt, prompt, max_tokens=6000)
