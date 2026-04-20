"""SkillDesigner Agent — purpose-built for writing/refining skill files."""
from __future__ import annotations
from .base_agent import BaseAgent


class SkillDesignerAgent(BaseAgent):
    ROLE = "Skill Designer"
    RULE_KEY = "skill_designer"

    def design_new_skill(self, agent_key: str, proposed_key: str,
                         pattern: str, task_sample: str) -> dict:
        """Produce a fresh skill file for a misfit pattern."""
        user = (
            f"Target agent: {agent_key}\n"
            f"Proposed skill_key: {proposed_key}\n\n"
            f"Misfit pattern (bug lặp again):\n{pattern}\n\n"
            f"Task mẫu from session:\n{task_sample}\n\n"
            "Viết skill file đầy enough per format BẮT BUỘC. "
            "Kết thúc bằng dòng CONFIDENCE: ... or ABORT: ..."
        )
        raw = self._call(self.system_prompt, user, max_tokens=2500)
        return self._parse(raw)

    def refine_existing(self, agent_key: str, skill_key: str,
                        current_content: str, recent_weaknesses: list[str],
                        avg_score: float) -> dict:
        """Produce a refined version of an existing skill."""
        weaknesses = "\n".join(f"- {w}" for w in recent_weaknesses[:6])
        user = (
            f"Target agent: {agent_key}\n"
            f"Skill current tại: {skill_key}  (avg score: {avg_score:.1f}/10)\n\n"
            f"Nội dung skill current tại:\n{current_content}\n\n"
            f"Các weakness lặp again in reviews gần here:\n{weaknesses}\n\n"
            "REFINE skill này: giữ phần tốt, cải tcurrent phần bị REVISE. "
            "No viết again toàn bộ if no need. "
            "Kết thúc bằng CONFIDENCE: ... or ABORT: ..."
        )
        raw = self._call(self.system_prompt, user, max_tokens=2500)
        return self._parse(raw)

    def design_merge(self, agent_key: str, skill_a_content: str,
                     skill_b_content: str) -> dict:
        """Produce a merged skill from two near-duplicates."""
        user = (
            f"Target agent: {agent_key}\n\n"
            f"SKILL A:\n{skill_a_content}\n\n---\n\n"
            f"SKILL B:\n{skill_b_content}\n\n"
            "2 skill này overlap lớn. MERGE thành 1 skill tốt hơn: "
            "gộp SCOPE & TRIGGERS no trùng, giữ phần output có giá trị nhất of cả hai. "
            "Kết thúc bằng CONFIDENCE: ... or ABORT: ..."
        )
        raw = self._call(self.system_prompt, user, max_tokens=3000)
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> dict:
        if "ABORT:" in raw:
            reason = raw.split("ABORT:", 1)[1].strip().splitlines()[0]
            return {"ok": False, "reason": reason, "content": "", "confidence": "LOW"}

        # Extract CONFIDENCE line (may be anywhere near end)
        import re
        conf_m = re.search(r"CONFIDENCE:\s*(HIGH|MEDIUM|LOW)\s*[—\-]\s*(.+)",
                           raw, re.IGNORECASE)
        confidence = conf_m.group(1).upper() if conf_m else "MEDIUM"
        rationale  = conf_m.group(2).strip() if conf_m else ""

        # Content = everything before CONFIDENCE line
        if conf_m:
            content = raw[:conf_m.start()].rstrip()
        else:
            content = raw.rstrip()

        # Ensure frontmatter exists
        if not content.lstrip().startswith("---"):
            return {"ok": False, "reason": "Missing frontmatter",
                    "content": content, "confidence": "LOW"}

        return {
            "ok":         True,
            "content":    content,
            "confidence": confidence,
            "rationale":  rationale,
        }
