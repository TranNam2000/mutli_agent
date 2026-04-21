"""Design Agent - UI/UX design specifications."""
from .base_agent import BaseAgent


def _task_field(task, name: str, default=None):
    """Get a field from either Task dataclass or dict."""
    if hasattr(task, name):
        return getattr(task, name)
    if isinstance(task, dict):
        return task.get(name, default)
    return default


class DesignAgent(BaseAgent):
    ROLE = "UI/UX Designer"
    RULE_KEY = "design"
    SKILL_KEY = "design"

    # ── Task-based flow ───────────────────────────────────────────────────────

    def process_ui_tasks(self, ui_tasks: list, existing_design_system: str = "") -> dict[str, str]:
        """
        For UI tasks:
          1. ONE batched LLM call → map every task to REUSE/CREATE
          2. For REUSE tasks → cheap reference
          3. For CREATE tasks → individual design spec (must-have detail)

        ~50% fewer LLM calls vs per-task checking.
        Returns {task_id: design_ref_markdown}.
        """
        if not ui_tasks:
            return {}

        refs: dict[str, str] = {}

        # Step 1: batched reuse decision
        if existing_design_system:
            reuse_map = self._batch_reuse_check(ui_tasks, existing_design_system)
        else:
            reuse_map = {}

        # Step 2: emit refs
        for task in ui_tasks:
            tid = _task_field(task, "id")
            if not tid:
                continue
            title = _task_field(task, "title", "")
            desc  = _task_field(task, "description", "")
            ac    = _task_field(task, "acceptance_criteria", [])

            decision = reuse_map.get(tid, {})
            if decision.get("reuse"):
                refs[tid] = (
                    f"[REUSE] {decision.get('component_name', '?')} — "
                    f"{decision.get('extension_notes', 'use nguyên')}"
                )
            else:
                # CREATE branch — individual call for detail
                spec = self._design_for_task(title, desc, ac, existing_design_system)
                refs[tid] = f"[NEW]\n{spec}"

        return refs

    def _batch_reuse_check(self, tasks: list, design_system: str) -> dict[str, dict]:
        """
        Single LLM call evaluating all UI tasks against design system.
        Output format (strict):
          TASK-001 | REUSE | <component_name> | <extension_notes>
          TASK-002 | CREATE | - | -
        """
        task_lines = []
        for t in tasks[:20]:  # safety cap
            tid   = _task_field(t, "id", "")
            title = _task_field(t, "title", "")
            desc  = _task_field(t, "description", "")[:100]
            task_lines.append(f"{tid}: {title} | {desc}")

        system = (
            "You are the Designer reviewing the UI task list. With mỗi task, quyết định có "
            "use again is component/screen from the current design system. "
            "Prefer REUSE — only CREATE when nothing truly matches."
        )
        prompt = f"""=== EXISTING DESIGN SYSTEM ===
{design_system[:4000]}

=== UI TASKS TO DECIDE ===
{chr(10).join(task_lines)}

REQUIRED output — one line per task, exact format (pipe-separated):
<TASK-ID> | <REUSE|CREATE> | <component_name or '-'> | <extension_notes or '-'>

Example:
TASK-001 | REUSE | PrimaryButton | need to add disabled state
TASK-002 | CREATE | - | -
TASK-003 | REUSE | ListItemCard | reuse as-is"""

        try:
            raw = self._call(system, prompt)
        except Exception:
            return {}

        result: dict[str, dict] = {}
        import re as _re
        for line in raw.splitlines():
            m = _re.match(
                r"\s*(TASK-[\w\-]+)\s*\|\s*(REUSE|CREATE)\s*\|\s*([^|]+?)\s*\|\s*(.+)",
                line,
            )
            if not m:
                continue
            tid, decision, name, notes = m.groups()
            result[tid] = {
                "reuse":            decision.upper() == "REUSE",
                "component_name":   name.strip() if name.strip() != "-" else "",
                "extension_notes":  notes.strip() if notes.strip() != "-" else "",
            }
        return result

    def _check_reuse(self, title: str, desc: str, design_system: str) -> dict:
        """Ask Claude: can existing design cover this task?"""
        system = (
            "You are the Designer. Check if the current design system has any component/screen "
            "reusable for this task. DO NOT create new if something can be reused."
        )
        prompt = f"""Task: {title}
Mô tả: {desc}

Current design system:
{design_system[:3000]}

Answer in exact format:
REUSE: YES|NO
COMPONENT_NAME: [existing component name if REUSE=YES]
EXTENSION_NOTES: [what to fix/extend if any]"""
        try:
            raw = self._call(system, prompt)
            reuse = "REUSE: YES" in raw
            name_m = __import__("re").search(r"COMPONENT_NAME:\s*(.+)", raw)
            ext_m  = __import__("re").search(r"EXTENSION_NOTES:\s*(.+)", raw)
            return {
                "reuse": reuse,
                "component_name": name_m.group(1).strip() if name_m else "",
                "extension_notes": ext_m.group(1).strip() if ext_m else "",
            }
        except Exception:
            return {"reuse": False}

    def _design_for_task(self, title: str, desc: str, ac: list[str],
                         design_system: str = "") -> str:
        """Create fresh design spec for 1 task, reusing tokens from existing DS."""
        ac_block = "\n".join(f"- {c}" for c in ac)
        ds_block = (f"\n\n=== EXISTING DESIGN SYSTEM (reuse tokens) ===\n{design_system[:2500]}"
                    if design_system else "")
        prompt = f"""Task: {title}
Mô tả: {desc}

Acceptance Criteria:
{ac_block}
{ds_block}

Build concise design spec for 1 task (≤ 40 lines):
- Screen layout (ASCII wireframe)
- Components needed + dimensions + states (loading/empty/error/success)
- Interactions + animations
- Reuse existing tokens if design system is provided"""
        return self._call(self.system_prompt, prompt)

    def clarify_with_ba(self, ba_agent: BaseAgent, prd: str) -> str:
        """Ask BA to clarify UX-related requirements before designing."""
        question_prompt = f"""Based on the PRD below, identify 1-2 points where Designer needs BA clarification before designing (e.g. user flow priority, UI edge cases, accessibility constraints...).

PRD (summary):
{prd[:1500]}"""
        question = self._call(
            f"You is {self.ROLE}. Ask a concise question (max 80 words) to BA about unclear UX requirements.",
            question_prompt,
        )
        return self.ask(ba_agent, question)

    def design(self, prd: str, project_plan: str) -> str:
        return self.design_with_clarification(prd, project_plan, "")

    def design_with_clarification(self, prd: str, project_plan: str, ba_clarification: str) -> str:
        clarification_block = f"\n=== BA CLARIFIED UX REQUIREMENTS ===\n{ba_clarification}" if ba_clarification else ""
        prompt = f"""Based on the PRD and Project Plan below, build full Design Specifications:

=== PRD ===
{prd[:2000]}...

=== PROJECT PLAN ===
{project_plan[:1500]}...{clarification_block}

Build detailed design specs including design system, wireframes for key screens, and component specifications. Focus on top-priority screens/features in Sprint 1."""
        return self._call(self.system_prompt, prompt)

    # 3 chiều thực sự đo is from ảnh tĩnh
    # Flow and contrast ratio chính xác no đo is from 1 screenshot
    _UI_WEIGHTS = {
        "fidelity":     0.35,  # màu/font/spacing/layout đúng specs
        "completeness": 0.35,  # enough screens + states (loading/empty/error/success)
        "heuristics":   0.30,  # consistency, feedback, error messages, hierarchy
    }
    _UI_PASS_THRESHOLD = 7

    def review_stitch_output(self, screenshot_path: str, design_specs: str,
                             user_journeys: str = "") -> dict:
        """
        3-dimension UI review from ảnh tĩnh:
        - Fidelity: màu/font/spacing đúng specs
        - Completeness: enough screens + states
        - Heuristics: consistency, feedback, visual hierarchy

        KHÔNG đo: contrast ratio chính xác, user flow (need prototype/nhiều ảnh).
        """
        system = (
            f"{self.system_prompt}\n\n"
            "You  review UI from ảnh tĩnh. Chỉ nhận xét những gì NHÌN THẤY RÕ RÀNG in ảnh. "
            "No đoán mò những gì no can xác định from ảnh tĩnh."
        )

        journey_block = (
            f"\n=== KEY USER JOURNEYS (context) ===\n{user_journeys[:800]}"
            if user_journeys else ""
        )

        prompt = f"""=== DESIGN SPECS ===
{design_specs}
{journey_block}

Review UI in ảnh per 3 chiều. Chỉ chấm dựa trên bằng chứng thực tế nhìn thấy in ảnh.

SCORE_FIDELITY: [1-10]
Căn cứ: Màu có khớp hex in design system? Font size/weight có đúng typography scale?
Spacing có per base-4px grid? Layout có đúng wireframe no?

SCORE_COMPLETENESS: [1-10]
Căn cứ: Có enough màn hình important no? Mỗi màn hình có các states need thiết no
(loading spinner, empty state placeholder, error message)? Component nào bị thiếu?

SCORE_HEURISTICS: [1-10]
Căn cứ (chỉ những gì nhìn thấy is):
- Visual hierarchy rõ ràng (heading > body > caption)?
- Consistent button styles, icon sizes, color usage?
- Interactive elements phân biệt is with non-interactive?
- Touch targets trông có enough to (≥ 44pt) no?

VERDICT: [PASS if weighted score ≥ {self._UI_PASS_THRESHOLD} | REVISE if < {self._UI_PASS_THRESHOLD}]
Weights: Fidelity=35% Completeness=35% Heuristics=30%

ISSUES:
- [FIDELITY] vấn đề cụ can with bằng chứng (e.g.: "button primary use #2196F3 nhưng specs is #1976D2")
- [COMPLETENESS] vấn đề cụ can (e.g.: "màn hình danh sách thiếu empty state")
- [HEURISTICS] vấn đề cụ can (e.g.: "3 button styles other nhau in cùng màn hình")

REVISION_GUIDE:
- [fix cụ can, actionable — enough to do ngay in Stitch]"""

        raw = self._call_with_image(system, prompt, screenshot_path)
        return self._parse_review(raw)

    def _parse_review(self, raw: str) -> dict:
        import re

        def _score(pattern: str) -> int:
            m = re.search(pattern, raw)
            return min(10, max(1, int(m.group(1)))) if m else 5

        fidelity     = _score(r"SCORE_FIDELITY:\s*(\d+)")
        completeness = _score(r"SCORE_COMPLETENESS:\s*(\d+)")
        heuristics   = _score(r"SCORE_HEURISTICS:\s*(\d+)")

        w = self._UI_WEIGHTS
        final = round(
            fidelity     * w["fidelity"] +
            completeness * w["completeness"] +
            heuristics   * w["heuristics"]
        )
        final = min(10, max(1, final))
        verdict = "PASS" if final >= self._UI_PASS_THRESHOLD else "REVISE"

        issues, guide = [], []
        section = None
        for line in raw.splitlines():
            l = line.strip()
            if "ISSUES:" in l:           section = "i"
            elif "REVISION_GUIDE:" in l: section = "g"
            elif l.startswith("- "):
                if section == "i": issues.append(l[2:])
                elif section == "g": guide.append(l[2:])

        return {
            "score":              final,
            "score_fidelity":     fidelity,
            "score_completeness": completeness,
            "score_heuristics":   heuristics,
            "verdict":            verdict,
            "issues":             issues,
            "revision_guide":     guide,
            "raw":                raw,
        }

    def build_stitch_prompt(self, design_specs: str) -> str:
        """Distill design specs into a concise Stitch prompt (≤500 words)."""
        system = (
            "You is UI/UX Designer. Tóm tắt design specs thành a prompt concise "
            "for Stitch AI to generate UI. Max 300 from. "
            "Bao gồm: name app, màu chính, font, layout chính, components important nhất."
        )
        return self._call(system, design_specs[:3000])

    def auto_stitch_loop(self, design_specs: str, session_id: str, max_rounds: int = 3) -> str | None:
        """
        Full auto loop:
          1. Build Stitch prompt from design specs
          2. Open Stitch browser, submit prompt, screenshot
          3. Review screenshot against design specs
          4. If REVISE: refine prompt and repeat
          5. Return final screenshot path when PASS or max_rounds reached
        """
        from testing.stitch_browser import generate_and_screenshot

        stitch_prompt = self.build_stitch_prompt(design_specs)
        print(f"\n  🤖 Stitch prompt generated ({len(stitch_prompt)} chars)")

        for round_num in range(1, max_rounds + 1):
            print(f"\n  {'─'*60}")
            print(f"  🌐 STITCH ROUND {round_num}/{max_rounds}")
            print(f"  {'─'*60}")

            screenshot_path = generate_and_screenshot(stitch_prompt, session_id, round_num)
            review = self.review_stitch_output(screenshot_path, design_specs)
            self.print_stitch_review(review, round_num)

            if review["verdict"] == "PASS":
                print(f"\n  ✅ UI đồng bộ design system! Screenshot: {screenshot_path}")
                return screenshot_path

            if round_num < max_rounds:
                # Refine prompt based on issues
                refine_system = (
                    "You is UI/UX Designer. Cải tcurrent Stitch prompt dựa trên các issues after. "
                    "Chỉ trả về prompt  fix, no giải thích."
                )
                issues_text = "\n".join(f"- {i}" for i in review["issues"])
                refine_input = f"Prompt gốc:\n{stitch_prompt}\n\nIssues need fix:\n{issues_text}"
                stitch_prompt = self._call(refine_system, refine_input)
                print(f"\n  🔄 Prompt refined, try again...")

        print(f"\n  ⚠️  Done {max_rounds} rounds — use screenshot cuối cùng.")
        return screenshot_path

    @staticmethod
    def _bar(score: int, width: int = 16) -> str:
        filled = round(score / 10 * width)
        return "█" * filled + "░" * (width - filled)

    def print_stitch_review(self, review: dict, round_num: int):
        icon = "✅" if review["verdict"] == "PASS" else "🔄"
        print(f"\n  {'─'*62}")
        print(f"  🎨 UI REVIEW [Round {round_num}]  {icon} {review['verdict']}  (pass≥{self._UI_PASS_THRESHOLD})")
        print(f"  {'─'*62}")

        dims = [
            ("Fidelity     ", "score_fidelity"),
            ("Completeness ", "score_completeness"),
            ("Accessibility", "score_accessibility"),
            ("Heuristics   ", "score_heuristics"),
            ("User Flow    ", "score_flow"),
        ]
        for label, key in dims:
            s = review.get(key, review["score"])
            warn = "  ⚠" if s < 7 else ""
            print(f"  {label}  {self._bar(s)}  {s:2}/10{warn}")

        print(f"  {'·'*62}")
        print(f"  Final        {self._bar(review['score'])}  {review['score']:2}/10")
        print(f"  {'─'*62}")

        if review["issues"]:
            print("  ⚠️  Issues:")
            for i in review["issues"][:6]:
                print(f"     • {i}")
        if review["verdict"] == "REVISE" and review["revision_guide"]:
            print("  📝 Sửa in Stitch:")
            for g in review["revision_guide"][:4]:
                print(f"     → {g}")
        print(f"  {'─'*62}")
