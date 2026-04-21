"""Dev Agent - Code implementation."""
from __future__ import annotations
from .base_agent import BaseAgent


class DevAgent(BaseAgent):
    ROLE = "Developer"
    RULE_KEY = "dev"
    SKILL_KEY = "dev"

    def inject_widget_keys(self, implementation: str, missing_keys: list[dict]) -> str:
        """
        Given existing Dev output + a list of missing widget keys (from QA test plan),
        ask LLM to patch the code adding Key('...') to the specified widgets.

        missing_keys: [{"key": "login_email_input", "widget_type": "TextField",
                        "file_hint": "login_page.dart", "purpose": "email input"}]
        """
        if not missing_keys:
            return implementation

        keys_block = "\n".join(
            f"- Key('{k['key']}') for {k.get('widget_type', '?')} in "
            f"{k.get('file_hint', '?')} — {k.get('purpose', '')}"
            for k in missing_keys
        )

        system = (
            f"You is {self.ROLE}. Add missing widget Key(...) to the code "
            "per the list. KEEP ALL other logic/structure unchanged, only add keys."
        )
        prompt = f"""=== CURRENT CODE ===
{implementation}

=== KEYS TO ADD ===
{keys_block}

Rules:
- Find correct widget per widget_type + file_hint + purpose
- If widget is inside a const constructor → remove const
- Use `const Key('...')` if possible; else `Key('...')`
- Group keys into `abstract class <Screen>Keys {{ }}` at file top if not present
- DO NOT change logic/layout/style

Output: full patched code, keep `// lib/...` comment at the top of each file."""
        try:
            return self._call(system, prompt)
        except Exception:
            # Fallback: return original unchanged
            return implementation

    def clarify_with_techlead(self, tl_agent: BaseAgent, tech_specs: str) -> str:
        """Ask Tech Lead to clarify ambiguous architecture decisions before coding."""
        question_prompt = f"""Based on the Technical Specs below, identify 1-2 technical points where Dev needs Tech Lead clarification before coding (e.g. API contract, error handling strategy, state management approach...).

Tech Specs (summary):
{tech_specs[:1500]}"""
        question = self._call(
            f"You is {self.ROLE}. Ask a concise question (max 80 words) to Tech Lead about unclear architecture points.",
            question_prompt,
        )
        return self.ask(tl_agent, question)

    def implement_with_clarification(
        self,
        tech_specs: str,
        design_specs: str,
        user_story: str,
        tl_clarification: str,
        tl_task_assignment: str = "",
    ) -> str:
        task_block = f"\n=== TASK ASSIGNMENT FROM TECH LEAD ===\n{tl_task_assignment}" if tl_task_assignment else ""
        prompt = f"""Implement the feature based on the specs below:

=== USER STORY ===
{user_story}

=== TECHNICAL SPECS (from Tech Lead) ===
{tech_specs[:2000]}...

=== DESIGN SPECS (from Designer) ===
{design_specs[:1000]}...

=== TECH LEAD ADDITIONAL CLARIFICATION ===
{tl_clarification}{task_block}

Implement fully with Flutter/Dart following Clean Architecture.
If info is missing to implement, note explicitly:
MISSING_INFO: [info needed] — MUST_ASK: [TechLead | BA | User]"""
        return self._call(self.system_prompt, prompt)
