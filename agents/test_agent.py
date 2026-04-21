"""QA/Test Agent - Test planning and quality assurance."""
from __future__ import annotations
from .base_agent import BaseAgent, _load_rule


class TestAgent(BaseAgent):
    ROLE = "QA/Tester"
    RULE_KEY = "test"  # fallback for legacy
    SKILL_KEY = "test"

    def _phase_prompt(self, phase: str) -> str:
        """Load rule for test_plan or test_review phase, fallback to test."""
        for key in [phase, "test"]:
            try:
                return _load_rule(key, self.profile)
            except FileNotFoundError:
                continue
        return ""

    # ── Task-based flow ───────────────────────────────────────────────────────

    def plan_from_sprint(self, sprint_plan, tasks: list) -> str:
        """
        Input: SprintPlan from TechLead + full task list.
        Output: test plan viết per thứ tự priority, with P0/P1 có enough test cases,
        P2/P3 chỉ need smoke check.
        """
        from learning.task_models import format_task_list

        # Group tasks by priority
        by_pri: dict[str, list] = {"P0": [], "P1": [], "P2": [], "P3": []}
        for t in tasks:
            by_pri.setdefault(t.priority.value, []).append(t)

        task_summary_lines = []
        for pri in ("P0", "P1", "P2", "P3"):
            if by_pri[pri]:
                task_summary_lines.append(f"\n### {pri} — {len(by_pri[pri])} tasks")
                for t in by_pri[pri]:
                    task_summary_lines.append(
                        f"  {t.id} ({t.type.value}/{t.module}): {t.title} [{t.complexity.value}]"
                    )
                    for ac in t.acceptance_criteria[:3]:
                        task_summary_lines.append(f"    AC: {ac}")

        task_summary = "\n".join(task_summary_lines)

        prompt = f"""Dựa into sprint plan and danh sách task  is TechLead prioritize,
viết test plan per thứ tự P0 → P1 → P2 → P3.

Quy tắc per priority:
- P0: enough happy + edge + negative + offline + performance check. Mỗi AC có ≥ 2 test cases.
- P1: enough happy + edge + negative. Mỗi AC có ≥ 1 test case.
- P2: chỉ need happy path + 1 edge case.
- P3: smoke check — chỉ verify task no crash app.

Tasks per priority:
{task_summary}

REQUIRED format:
## TC-001 | task=TASK-XXX | pri=P0 | type=unit|widget|integration|e2e
**Given:** ...
**When:** ...
**Then:** ...
**Expected:** ...

Cuối output: Exit criteria + Performance targets for P0 tasks.

Cũng output Patrol code (```dart```) and Maestro YAML (```yaml```) for các TC P0 + P1.
Maestro flow đặt in comment: `# maestro/<filename>.yaml`
"""
        return self._call(self._phase_prompt("test_plan"), prompt)

    def verify_edge_cases(self, dev_agent: BaseAgent, implementation: str) -> str:
        """Ask Dev about edge cases and error handling in the implementation."""
        question_prompt = f"""Based on the implementation below, identify 1-2 edge cases or error scenarios where QA wants Dev to confirm specific behavior before writing test cases.

Implementation:
{implementation}"""
        question = self._call(
            f"You is {self.ROLE}. Ask a concise question (max 80 words) to Dev about edge cases or error handling behavior.",
            question_prompt,
        )
        return self.ask(dev_agent, question)

    def create_test_plan(self, prd: str, tech_specs: str, implementation: str) -> str:
        prompt = f"""Build a full Test Plan and Test Cases based on:

=== PRD & REQUIREMENTS ===
{prd}

=== TECHNICAL IMPLEMENTATION ===
{tech_specs}

=== CODE IMPLEMENTATION ===
{implementation}

Build comprehensive test strategy, detailed test cases (including happy path, edge cases, negative cases), performance test scenarios, and security checklist. Ensure coverage for all acceptance criteria in PRD."""
        return self._call(self.system_prompt, prompt)

    def create_test_plan_with_context(self, prd: str, tech_specs: str, implementation: str, dev_clarification: str) -> str:
        prompt = f"""Build a full Test Plan and Test Cases based on:

=== PRD & REQUIREMENTS ===
{prd}

=== TECHNICAL IMPLEMENTATION ===
{tech_specs}

=== CODE IMPLEMENTATION ===
{implementation}

=== DEV CONFIRMED EDGE CASES ===
{dev_clarification}

Build comprehensive test strategy, taking into account edge cases Dev confirmed."""
        return self._call(self.system_prompt, prompt)

    # ── Phase 1: Write test cases from requirements (before implementation) ──

    def clarify_with_ba(self, ba_agent: BaseAgent, prd: str) -> str:
        """Ask BA about acceptance criteria before writing test plan."""
        question_prompt = f"""Based on the PRD below, identify 1-2 points to clarify about acceptance criteria or scope before writing test cases.

PRD:
{prd}"""
        question = self._call(
            f"You is {self.ROLE}. Concise question (max 80 words) for BA about acceptance criteria.",
            question_prompt,
        )
        return self.ask(ba_agent, question)

    def write_test_plan(self, prd: str, project_plan: str, tech_specs: str = "", ba_clarification: str = "") -> str:
        """Phase 1 — Write test cases from requirements, before seeing implementation."""
        tech_block = f"\n=== TECHNICAL SPECS (API contracts, error codes, data model) ===\n{tech_specs}" if tech_specs else ""
        prompt = f"""Write Test Plan and Test Cases BASED ON REQUIREMENTS (no implementation yet).
Goal: verify requirements are met, NOT verify what the code does.

=== PRD & REQUIREMENTS ===
{prd}

=== USER STORIES & ACCEPTANCE CRITERIA ===
{project_plan}{tech_block}

=== BA CLARIFICATION ===
{ba_clarification if ba_clarification else "(none)"}"""
        return self._call(self._phase_prompt("test_plan"), prompt)

    # ── Phase 2: Verify implementation against test plan ────────────────────

    def review_implementation(self, test_plan: str, implementation: str, dev_clarification: str = "") -> str:
        """Phase 2 — Execute test plan against implementation + generate runnable Flutter test code."""
        prompt = f"""Compare implementation to test plan. Report PASS/FAIL and write runnable Flutter test code.

=== TEST PLAN (written from requirements, before code) ===
{test_plan}

=== IMPLEMENTATION ===
{implementation}

=== DEV CONFIRMED EDGE CASES ===
{dev_clarification if dev_clarification else "(none)"}

REQUIRED format — 6 sections:

## ✅ PASSED
TC-[ID]: [Name] — [brief pass reason]

## ❌ FAILED
TC-[ID]: [Name]
  Expected: [expected result per test plan]
  Actual: [what the code does differently]
  Severity: BLOCKER | CRITICAL | MAJOR | MINOR

## ⚠️ NOT TESTABLE
TC-[ID]: [Name] — [where implementation is missing]

## 📊 SUMMARY
Total: X | Passed: X | Failed: X | Not Testable: X | Pass Rate: X%
Blockers: X | Critical: X

## 🔧 FIXES REQUIRED
[Concrete list Dev must fix, BLOCKER first]

## 🧪 FLUTTER TEST CODE
Write runnable Dart test code for the most critical TCs (P0+P1).
Include both widget tests and integration tests where appropriate.

```dart
// widget_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
// import các file need thiết from implementation

void main() {{
  // Widget tests — test each widget individually
  group('[FeatureName] Widget Tests', () {{
    testWidgets('TC-001: [Name test case]', (WidgetTester tester) async {{
      // Arrange
      await tester.pumpWidget(/* widget need test */);

      // Act
      await tester.tap(find.byKey(const Key('...')));
      await tester.pump();

      // Assert
      expect(find.text('...'), findsOneWidget);
    }});
  }});

  // Integration tests — test the full flow
  group('[FeatureName] Integration Tests', () {{
    testWidgets('TC-00X: [Happy path end-to-end]', (WidgetTester tester) async {{
      // setup → action → verify
    }});
  }});
}}
```

Request test code:
- Use correct widget keys from implementation (if any), or propose keys to add
- Cover happy path + critical edge cases
- Each test: explicit Arrange / Act / Assert
- Include comment explaining which TC-ID this test verifies"""
        return self._call(self._phase_prompt("test_review"), prompt)
