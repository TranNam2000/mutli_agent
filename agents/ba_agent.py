"""BA (Business Analyst) Agent - Requirements gathering and analysis."""
from __future__ import annotations
import re
from .base_agent import BaseAgent


class BAAgent(BaseAgent):
    ROLE = "Business Analyst (BA)"
    RULE_KEY = "ba"
    SKILL_KEY = "ba"

    # ── Clarification Gate ────────────────────────────────────────────────────

    def check_clarity(self, product_idea: str) -> dict:
        """
        Check if input is clear enough to produce a full PRD.
        Returns: {is_clear: bool, questions: list[str]}
        """
        prompt = f"""Evaluate clarity of the following product/task request:

{product_idea}

The request is considered CLEAR when it has:
- Scope: knows what to do and not do
- Users: who uses it, for what
- Goals: measurable outcome
- Constraints: technical, time, or budget (at least one)

If CLEAR → return exactly:
CLEAR: YES

If ambiguous → list 3-5 specific questions for the stakeholder:
CLEAR: NO
QUESTIONS:
1. [specific question — not generic]
2. [question cụ can]
3. [question cụ can]"""

        raw = self._call(self.system_prompt, prompt)

        is_clear = "CLEAR: YES" in raw
        questions = []
        if not is_clear:
            for line in raw.splitlines():
                m = re.match(r"^\s*\d+\.\s*(.+)", line.strip())
                if m:
                    questions.append(m.group(1).strip())

        return {"is_clear": is_clear, "questions": questions, "raw": raw}

    def enrich_input(self, product_idea: str, qa_pairs: list[dict]) -> str:
        """Merge original input with user's answers into enriched task description."""
        if not qa_pairs:
            return product_idea
        clarifications = "\n".join(
            f"Q: {pair['q']}\nA: {pair['a']}" for pair in qa_pairs
        )
        return (
            f"{product_idea}\n\n"
            f"## Clarifications from Stakeholder\n{clarifications}"
        )

    # ── Impact Assessment (Update mode) ──────────────────────────────────────

    # Sections relevant for impact check per step (avoid reading full docs)
    _IMPACT_SECTIONS = {
        "ba":       ["scope", "features", "requirements", "acceptance criteria", "risks"],
        "pm":       ["roadmap", "sprint", "user stories", "dependencies"],
        "design":   ["screens", "components", "user flows"],
        "techlead": ["api", "schema", "architecture", "tech stack"],
        "dev":      ["implementation plan", "file list", "known limitations"],
        "test":     ["test strategy", "exit criteria", "scope", "passed", "failed", "blockers"],
    }

    def assess_impact(self, task: str, existing_docs: dict[str, str]) -> dict:
        """
        Read existing pipeline docs, assess which steps need re-run for this task.
        Returns: {affected, unchanged, reasons, summary}
        """
        doc_summary = self._build_doc_summary(existing_docs)

        prompt = f"""New task / change to process:
{task}

=== CURRENT DOCS (relevant excerpts) ===
{doc_summary}

Assess: which pipeline steps does this task affect?
Steps: ba | pm | design | techlead | dev | test

Rules:
- AFFECTED: step whose doc content must change due to new task
- UNCHANGED: step not affected, doc still valid
- Mark AFFECTED only with concrete evidence from docs

REQUIRED format:
AFFECTED: [comma-separated list | NONE]
UNCHANGED: [comma-separated list | NONE]
REASON_ba: [short reason | N/A]
REASON_pm: [lý do | N/A]
REASON_design: [lý do | N/A]
REASON_techlead: [lý do | N/A]
REASON_dev: [lý do | N/A]
REASON_test: [lý do | N/A]
SUMMARY: [1-2 sentence summarizing change scope]"""

        raw = self._call(self.system_prompt, prompt)
        return self._parse_impact(raw)

    def _build_doc_summary(self, docs: dict[str, str]) -> str:
        parts = []
        for key, content in docs.items():
            if not content or content.startswith("[SKIPPED"):
                continue
            keywords = self._IMPACT_SECTIONS.get(key, [])
            excerpt  = self._pick_sections(content, keywords, max_chars=600)
            parts.append(f"### [{key.upper()}]\n{excerpt}")
        return "\n\n".join(parts)

    def _pick_sections(self, text: str, keywords: list[str], max_chars: int) -> str:
        lines = text.splitlines()
        result, budget, collecting = [], max_chars, False
        for line in lines:
            stripped   = line.lstrip("#").strip().lower()
            is_header  = line.startswith("#")
            if is_header:
                collecting = any(kw in stripped for kw in keywords)
            if collecting:
                entry = line + "\n"
                if budget <= 0:
                    result.append("...[truncated]")
                    break
                result.append(entry)
                budget -= len(entry)
        return "".join(result) if result else text[:max_chars]

    def _parse_impact(self, raw: str) -> dict:
        all_steps = ["ba", "pm", "design", "techlead", "dev", "test"]

        def _list(pattern: str) -> list[str]:
            m = re.search(pattern, raw, re.IGNORECASE)
            if not m or m.group(1).strip().upper() == "NONE":
                return []
            return [s.strip().lower() for s in m.group(1).split(",") if s.strip()]

        affected  = _list(r"AFFECTED:\s*(.+)")
        unchanged = _list(r"UNCHANGED:\s*(.+)")

        if affected and not unchanged:
            unchanged = [s for s in all_steps if s not in affected]
        elif unchanged and not affected:
            affected  = [s for s in all_steps if s not in unchanged]
        elif not affected and not unchanged:
            affected, unchanged = all_steps, []  # safe fallback

        reasons = {}
        for step in all_steps:
            m = re.search(rf"REASON_{step}:\s*(.+)", raw, re.IGNORECASE)
            reasons[step] = m.group(1).strip() if m else ""

        summary_m = re.search(r"SUMMARY:\s*(.+)", raw)
        return {
            "affected":  affected,
            "unchanged": unchanged,
            "reasons":   reasons,
            "summary":   summary_m.group(1).strip() if summary_m else "",
        }

    _PIPELINE_STEPS = ["ba", "pm", "design", "techlead", "dev", "test"]

    def print_impact(self, assessment: dict):
        affected  = set(assessment["affected"])
        unchanged = set(assessment["unchanged"])

        print(f"\n  {'═'*60}")
        print(f"  🎯 IMPACT ASSESSMENT")
        print(f"  {'═'*60}")
        if assessment["summary"]:
            print(f"  {assessment['summary']}")

        # ASCII pipeline diagram
        print(f"\n  Pipeline:")
        nodes = []
        for step in self._PIPELINE_STEPS:
            if step in affected:
                nodes.append(f"[🔄{step}]")
            elif step in unchanged:
                nodes.append(f"[✅{step}]")
            else:
                nodes.append(f"[··{step}]")
        # Design + TechLead are parallel
        ba, pm, design, tl, dev, test = nodes
        print(f"  {ba} ──► {pm} ──► {design} ┐")
        print(f"  {'':>{len(ba)+7}}{tl} ┘──► {dev} ──► {test}")

        # Legend + reasons
        print(f"\n  🔄 Needs update : {', '.join(sorted(affected))  or 'none'}")
        print(f"  ✅ Unchanged   : {', '.join(sorted(unchanged)) or 'none'}")
        for step in self._PIPELINE_STEPS:
            if step in affected:
                r = assessment["reasons"].get(step, "")
                if r and r.upper() != "N/A":
                    print(f"    → [{step:8}] {r}")
        print(f"  {'═'*60}")

    # ── Feedback Assessment ───────────────────────────────────────────────────

    _FEEDBACK_DEFAULT_AFFECTED = {
        "Bug":             ["dev", "test"],
        "UX Issue":        ["design", "dev", "test"],
        "Missing Feature": ["ba", "pm", "design", "techlead", "dev", "test"],
        "Performance":     ["techlead", "dev", "test"],
        "Other":           ["ba", "pm", "design", "techlead", "dev", "test"],
    }

    def assess_feedback(self, feedback_task: str, existing_docs: dict[str, str], feedback_type: str) -> dict:
        """
        Assess which pipeline steps need re-run based on product feedback.
        More targeted than assess_impact — uses feedback_type as prior.
        """
        doc_summary = self._build_doc_summary(existing_docs)
        default_affected = self._FEEDBACK_DEFAULT_AFFECTED.get(feedback_type, [])

        prompt = f"""User just reported feedback from live product:

FEEDBACK TYPE: {feedback_type}
DESCRIPTION: {feedback_task[:800]}

=== TÀI LIỆU HIỆN CÓ ===
{doc_summary}

Based on feedback type "{feedback_type}", identify which steps need updating.
Initial suggestion: {', '.join(default_affected)} — but adjust based on docs if needed.

Steps: ba | pm | design | techlead | dev | test_plan | test_review

REQUIRED format:
AFFECTED: [danh sách | NONE]
UNCHANGED: [danh sách | NONE]
REASON_ba: [lý do | N/A]
REASON_pm: [lý do | N/A]
REASON_design: [lý do | N/A]
REASON_techlead: [lý do | N/A]
REASON_dev: [lý do | N/A]
REASON_test_plan: [lý do | N/A]
REASON_test_review: [lý do | N/A]
SUMMARY: [1-2 câu describe change scope]"""

        raw = self._call(self.system_prompt, prompt)
        result = self._parse_impact(raw)

        # Fallback: if parse failed, use default based on feedback type
        if not result["affected"]:
            _all = ["ba", "pm", "design", "techlead", "dev", "test"]
            result["affected"]  = default_affected
            result["unchanged"] = [s for s in _all if s not in default_affected]
        return result

    # ── Main analysis ─────────────────────────────────────────────────────────

    def analyze(self, product_idea: str) -> str:
        prompt = f"""Stakeholder request:

{product_idea}

Analyze the request and write the feature description per your rule.
Lưu ý:
- If input contains 'Stakeholder Clarifications' → read carefully and use; DO NOT ask already-answered items
- Scale output based on actual complexity (1 screen → concise, multi-feature → more complete)"""
        return self._call(self.system_prompt, prompt)

    # ── Task-based flow ───────────────────────────────────────────────────────

    def produce_tasks(self, product_idea: str) -> str:
        """Task-based output: structured TASK list with classification.
        Uses the `task_based` skill explicitly (overrides auto-detect)."""
        # Force the task_based skill
        try:
            from learning.skill_selector import list_skills
            for skill in list_skills("ba"):
                if skill["skill_key"] == "task_based":
                    self._active_skill = skill
                    self._active_skill["detected_scope"] = "feature"
                    self._active_skill["selection_method"] = "forced"
                    break
        except Exception:
            pass

        prompt = f"""Stakeholder task / request:

{product_idea}

Produce the TASK list in the skill's exact format (## TASK-XXX | type=... | priority=... | ...).
Each task has full AC (Given/When/Then), classified type (ui|logic|bug|hotfix|mixed), priority (P0-P3), complexity (S/M/L), risk (low/med/high).
If task has both new UI and new logic → use type=mixed.
If info missing → append MISSING_INFO: ... — MUST_ASK: ... at end of output.

After each task body, emit a fenced metadata block with this EXACT schema so the
orchestrator can make skip-Critic decisions:

```json META
{{
  "task_id": "<TASK-XXX from header>",
  "context": {{
    "scope":      "feature|bug_fix|hotfix|refactor|ui_tweak|investigation",
    "priority":   "P0|P1|P2|P3",
    "risk_level": "low|med|high",
    "complexity": "S|M|L|XL"
  }},
  "flow_control": {{
    "skip_critic":   [],
    "require_qa":    true,
    "max_revisions": 2
  }},
  "technical_debt": {{
    "impact_area":     ["ui|state_management|api|payment|auth|core|..."],
    "legacy_affected": false
  }}
}}
```

Metadata rules:
- `impact_area` must list concrete modules the task touches. Include "payment",
  "auth", or "core" when relevant — those force Critic even on tiny tweaks.
- Set `flow_control.skip_critic = ["PM","BA","TechLead"]` ONLY when ALL of these
  hold: complexity=S, risk_level=low, impact_area lacks payment/auth/core.
- Hotfix P0 → `skip_critic = ["PM","BA","TechLead"]` (live-incident Fast-Track).
"""
        return self._call(self.system_prompt, prompt)

    def consolidate_tasks(self, tasks_markdown: str, design_refs: dict[str, str]) -> str:
        """
        After when Design done UI tasks, merge design_refs into task list.
        design_refs = {TASK_ID: "đường dẫn/tham chiếu design"}
        """
        if not design_refs:
            return tasks_markdown

        # Simple line-level injection: find TASK-XXX header, add design ref after **Title:**
        import re as _re
        updated: list[str] = []
        lines = tasks_markdown.splitlines()
        i = 0
        current_id: str | None = None
        while i < len(lines):
            line = lines[i]
            m = _re.match(r"^##\s+(TASK-\w+)\s*\|", line)
            if m:
                current_id = m.group(1)
            updated.append(line)
            # After **Title:** line, inject **Design ref:**
            if current_id and line.strip().startswith("**Title:**") and current_id in design_refs:
                updated.append(f"**Design ref:** {design_refs[current_id]}")
                # Mark as consumed so we don't inject twice
                design_refs.pop(current_id, None)
            i += 1
        return "\n".join(updated)

    def revise_specs(self, current_tasks_md: str, postmortem: str,
                       stuck_task_ids: list[str] | None = None) -> str:
        """
        Option C — spec postmortem. Called when Dev+QA have looped ≥ 2 rounds
        on the same BLOCKER. TL has asked BA to reflect; this method rewrites
        ONLY the affected tasks' specs with extra AC / edge-cases based on the
        postmortem. Other tasks are preserved untouched.

        Args:
          current_tasks_md: current ba.md content (the whole task list)
          postmortem: BA's answer to TL's postmortem question
          stuck_task_ids: tasks that are known to be stuck (hint for BA)

        Returns: full revised ba.md content — replaces self.results['ba'].
        """
        stuck = ", ".join(stuck_task_ids or [])
        prompt = f"""You are BA. QA + Dev are stuck on some tasks after 2 revise rounds.
Tech Lead's postmortem summary:

{postmortem}

Affected task IDs (if known): {stuck or "(unknown — infer from postmortem)"}

=== CURRENT FULL TASK LIST ===
{current_tasks_md}

Rewrite the FULL task list with the following rules:
- For tasks mentioned in the postmortem: add missing AC, call out edge cases,
  tighten description. Keep `## TASK-XXX | ...` header format intact.
- For other tasks: keep EXACTLY as-is (do not touch).
- Preserve any ```json META``` blocks except you MAY bump `risk_level` to
  `high` on the affected task(s).
- Never delete tasks.

Return the revised ba.md content only — no prose around it."""
        return self._call(self.system_prompt, prompt)

    def revise_with_answers(self, original_output: str, resolved: dict[str, str], original_prompt: str) -> str:
        """Revise BA output after when có answer for MISSING_INFO — tổng hợp sạch, phân cấp rõ."""
        answers_text = "\n".join(f"- {info}: {ans}" for info, ans in resolved.items())
        prompt = f"""Current BA document:
{original_output}

Answers to remaining questions:
{answers_text}

Rewrite the BA document with full updates:
- Integrate all answers in the right places
- Remove all MISSING_INFO that have been answered
- Clear hierarchy, concise
- Keep format per the rule"""
        return self._call(self.system_prompt, prompt)
