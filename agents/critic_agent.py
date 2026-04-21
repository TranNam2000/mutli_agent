"""Critic Agent — checklist-based scoring (YES/NO per item, code tính điểm)."""
from __future__ import annotations
import math
import re
from pathlib import Path
from .base_agent import BaseAgent, _RULES_DIR

_DEFAULT_THRESHOLD = 7
_DEFAULT_WEIGHTS   = (0.40, 0.20, 0.40)


# ── Criteria parsing ──────────────────────────────────────────────────────────

def _parse_criteria_meta(criteria_text: str) -> tuple[int, tuple[float, float, float]]:
    threshold = _DEFAULT_THRESHOLD
    weights   = _DEFAULT_WEIGHTS

    m = re.search(r"PASS_THRESHOLD:\s*(\d+)", criteria_text)
    if m:
        threshold = int(m.group(1))

    m = re.search(
        r"WEIGHTS:\s*completeness=([\d.]+)\s+format=([\d.]+)\s+quality=([\d.]+)",
        criteria_text,
    )
    if m:
        c, f, q = float(m.group(1)), float(m.group(2)), float(m.group(3))
        total = c + f + q or 1
        weights = (c / total, f / total, q / total)

    return threshold, weights


def _extract_checklist(criteria_text: str) -> dict[str, list[str]]:
    """
    Parse checklist items per dimension from criteria file.
    Looks for lines starting with '- [ ]' under ## Completeness / ## Format / ## Quality sections.
    Returns {completeness: [...], format: [...], quality: [...]}
    """
    items: dict[str, list[str]] = {"completeness": [], "format": [], "quality": []}
    section = None
    for line in criteria_text.splitlines():
        lower = line.lstrip("#").strip().lower()
        if line.startswith("#"):
            if "completeness" in lower:
                section = "completeness"
            elif "format" in lower:
                section = "format"
            elif "quality" in lower:
                section = "quality"
            else:
                section = None
        elif section and re.match(r"^\s*-\s*(\[[ x]\]\s*)?(.+)", line):
            m = re.match(r"^\s*-\s*(?:\[[ x]\]\s*)?(.+)", line)
            if m:
                items[section].append(m.group(1).strip())
    return items


def _load_criteria(key: str, profile: str = "default") -> str:
    for p in [profile, "default"]:
        path = _RULES_DIR / p / "criteria" / f"{key}.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


# ── Critic Agent ──────────────────────────────────────────────────────────────

class CriticAgent(BaseAgent):
    ROLE     = "Critic/Reviewer"
    RULE_KEY = "critic"
    MAX_ROUNDS = 2

    def evaluate(self, agent_role: str, output: str, agent_key: str,
                 original_context: str = "") -> dict:
        criteria  = _load_criteria(agent_key, self.profile)
        threshold, (wc, wf, wq) = _parse_criteria_meta(criteria)
        checklist = _extract_checklist(criteria)

        context_block = (
            f"\n=== YÊU CẦU / CONTEXT GỐC ===\n{original_context}\n"
            if original_context else ""
        )

        # Build YES/NO checklist prompt
        checklist_block = self._build_checklist_prompt(checklist)

        prompt = f"""Review output of {agent_role}.
{context_block}
=== OUTPUT ===
{output}

=== CHECKLIST — chấm PARTIAL CREDIT ===
With mỗi mục, trả về 1 in 3 mức (đừng gian lận — choose MISS when thực sự thiếu):
  FULL    —  done fully, good quality
  PARTIAL — có do nhưng thiếu/sơ sài (≥50% nhưng <100%)
  MISS    — thiếu hoàn toàn or sai

{checklist_block}

After checklist, viết thêm:
FAILED_ITEMS:
- [item MISS/PARTIAL → lý do cụ can with bằng chứng from output]

REVISION_GUIDE:
- [hành động cụ can to fix]

ASSUMPTIONS_FOUND:
- [content] — [OK | SILENT | MISSING]"""

        raw = self._call(self.system_prompt, prompt)
        return self._parse(raw, checklist, threshold, wc, wf, wq)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _build_checklist_prompt(checklist: dict[str, list[str]]) -> str:
        lines = []
        idx = 1
        for dim, items in checklist.items():
            if not items:
                continue
            lines.append(f"## {dim.upper()}")
            for item in items:
                lines.append(f"C{idx}: {item}")
                idx += 1
        return "\n".join(lines)

    def _parse(self, raw: str, checklist: dict[str, list[str]],
               threshold: int, wc: float, wf: float, wq: float) -> dict:
        """
        Count YES/NO answers → calculate score per dimension.
        Score = (yes_count / total_items) * 10, floor'd.
        """
        # Map item index → dimension
        item_dim: list[str] = []
        for dim in ("completeness", "format", "quality"):
            item_dim.extend([dim] * len(checklist[dim]))

        # Parse graded answers: FULL=1.0, PARTIAL=0.5, MISS=0.0.
        # Backward compat: YES=FULL, NO=MISS.
        answers_graded: dict[int, float] = {}
        answers: dict[int, bool] = {}  # kept for revise_history compat
        for m in re.finditer(r"C(\d+)\s*:\s*(FULL|PARTIAL|MISS|YES|NO)", raw, re.IGNORECASE):
            idx = int(m.group(1))
            label = m.group(2).upper()
            if label in ("FULL", "YES"):
                answers_graded[idx] = 1.0
                answers[idx] = True
            elif label == "PARTIAL":
                answers_graded[idx] = 0.5
                answers[idx] = False  # partial doesn't count as pass for old logic
            else:  # MISS / NO
                answers_graded[idx] = 0.0
                answers[idx] = False

        # Count per dimension using graded points
        dim_pts   = {"completeness": 0.0, "format": 0.0, "quality": 0.0}
        dim_yes   = {"completeness": 0, "format": 0, "quality": 0}  # full PASS only
        dim_total = {"completeness": 0, "format": 0, "quality": 0}
        for i, dim in enumerate(item_dim, start=1):
            dim_total[dim] += 1
            pts = answers_graded.get(i, 0.0)
            dim_pts[dim] += pts
            if pts == 1.0:
                dim_yes[dim] += 1

        def _score(dim: str) -> int:
            total = dim_total[dim]
            if total == 0:
                return 7  # no items → neutral
            return math.floor((dim_pts[dim] / total) * 10)

        completeness = _score("completeness")
        fmt          = _score("format")
        quality      = _score("quality")

        # Apply penalty rules from criteria (MISSING_INFO, short output)
        if re.search(r"MISSING_INFO", raw, re.IGNORECASE):
            quality = min(quality, 4)
        if len(raw) < 200:
            quality = min(quality, 3)

        final   = math.floor(completeness * wc + fmt * wf + quality * wq)
        final   = min(10, max(1, final))
        verdict = "PASS" if final >= threshold else "REVISE"

        # Parse failed items, revision guide, assumptions
        failed, guide, assumptions, strengths = [], [], [], []
        section = None
        for line in raw.splitlines():
            l = line.strip()
            if "FAILED_ITEMS:"    in l: section = "f"
            elif "REVISION_GUIDE:" in l: section = "g"
            elif "ASSUMPTIONS_FOUND:" in l: section = "a"
            elif l.startswith("- "):
                item = l[2:]
                if section == "f": failed.append(item)
                elif section == "g": guide.append(item)
                elif section == "a": assumptions.append(item)

        # Strengths = items that passed
        for i, dim in enumerate(item_dim, start=1):
            if answers.get(i, False) and i <= len(checklist.get(dim, [])):
                dim_items = checklist[dim]
                local_idx = i - sum(
                    len(checklist[d]) for d in ("completeness", "format", "quality")
                    if list(checklist).index(d) < list(checklist).index(dim)
                )
                if 1 <= local_idx <= len(dim_items):
                    strengths.append(dim_items[local_idx - 1])

        # Fallback strengths from passed C-items
        if not strengths:
            passed_count = sum(1 for v in answers.values() if v)
            if passed_count:
                strengths = [f"{passed_count}/{len(item_dim)} checklist items passed"]

        checklist_flat = []
        for dim in ("completeness", "format", "quality"):
            checklist_flat.extend(checklist[dim])

        return {
            "score":              final,
            "score_completeness": completeness,
            "score_format":       fmt,
            "score_quality":      quality,
            "verdict":            verdict,
            "pass_threshold":     threshold,
            "strengths":          strengths,
            "weaknesses":         failed,
            "revision_guide":     guide,
            "assumptions":        assumptions,
            "checklist_answers":  answers,
            "checklist_flat":     checklist_flat,
            "raw":                raw,
        }

    # ── Display ───────────────────────────────────────────────────────────────

    @staticmethod
    def _score_bar(score: int, width: int = 20) -> str:
        filled = round(score / 10 * width)
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def _score_label(score: int) -> str:
        if score >= 9: return "excellent"
        if score >= 7: return "good"
        if score >= 5: return "fair"
        return "weak ⚠"

    def print_review(self, agent_role: str, review: dict, round_num: int):
        verdict_icon = "✅" if review["verdict"] == "PASS" else "🔄"
        c     = review["score_completeness"]
        f     = review["score_format"]
        q     = review["score_quality"]
        final = review["score"]
        threshold = review.get("pass_threshold", _DEFAULT_THRESHOLD)

        # Checklist summary
        answers   = review.get("checklist_answers", {})
        yes_count = sum(1 for v in answers.values() if v)
        total     = len(answers)

        print(f"\n  {'─'*62}")
        print(f"  🔍 CRITIC REVIEW — {agent_role}  [Round {round_num}]  {verdict_icon} {review['verdict']}  (pass≥{threshold})")
        print(f"  Checklist: {yes_count}/{total} items passed")
        print(f"  {'─'*62}")
        print(f"  Completeness  {self._score_bar(c)}  {c:2}/10  {self._score_label(c)}")
        print(f"  Format        {self._score_bar(f)}  {f:2}/10  {self._score_label(f)}")
        print(f"  Quality       {self._score_bar(q)}  {q:2}/10  {self._score_label(q)}")
        print(f"  {'·'*62}")
        print(f"  Final         {self._score_bar(final)}  {final:2}/10")
        print(f"  {'─'*62}")
        if review["weaknesses"]:
            print("  ❌ Failed items:")
            for w in review["weaknesses"][:4]:
                print(f"     • {w}")
        if review["verdict"] == "REVISE" and review["revision_guide"]:
            print("  📝 Need fix:")
            for g in review["revision_guide"][:3]:
                print(f"     → {g}")
        if review.get("assumptions"):
            print("  📌 Assumptions:")
            for a in review["assumptions"][:2]:
                icon = "🔴" if "WRONG" in a.upper() else ("🟡" if "SILENT" in a.upper() else "🟢")
                print(f"     {icon} {a}")
        print(f"  {'─'*62}")
