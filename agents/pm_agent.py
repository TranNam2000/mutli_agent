"""
PM (Project Manager) Agent — routes an incoming request to the right sub-pipeline.

Responsibilities:
  1. Read the raw request and assign it a `kind`:
        feature | bug_fix | ui_tweak | refactor | investigation
  2. Decompose bundled requests into independent sub-tasks when needed.
  3. Return a dispatch plan — the list of downstream steps the orchestrator
     should run for this kind.

Cost control:
  - Fast heuristic layer (keyword match) decides first.
  - LLM fallback only when heuristic is ambiguous or no keyword wins.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from core.logging import tprint
from .base_agent import BaseAgent


# Canonical kinds — keep in sync with rules/default/pm.md.
KIND_FEATURE       = "feature"
KIND_BUG_FIX       = "bug_fix"
KIND_UI_TWEAK      = "ui_tweak"
KIND_REFACTOR      = "refactor"
KIND_INVESTIGATION = "investigation"
ALL_KINDS = (
    KIND_FEATURE, KIND_BUG_FIX, KIND_UI_TWEAK, KIND_REFACTOR, KIND_INVESTIGATION,
)

# All recognised pipeline step keys (matches orchestrator.STEP_KEYS minus PM).
# Used as a final safety fallback when both the LLM step picker and the
# heuristic give nothing — the runner just executes everything in order.
ALL_STEPS: list[str] = ["ba", "design", "techlead", "test_plan", "dev", "test"]

# Investigation has its own single step (no Dev/Test) and is recognised by
# kind directly in run_investigation_path — kept here for clarity.
INVESTIGATION_STEPS: list[str] = ["investigation"]

# Heuristic keywords — used before hitting the LLM. Keywords are matched
# against the lowercased, diacritic-stripped request, so Vietnamese variants
# like "đổi màu" / "doi mau" / "dổi mau" all hit the same entry.
_KEYWORDS: dict[str, list[str]] = {
    KIND_BUG_FIX: [
        "bug", "fix", "crash", "error", "broken", "not working", "doesn't work",
        "regression", "stack trace", "null pointer", "exception", "hotfix",
        "loi", "sua loi", "khac phuc", "khong chay", "treo", "loi crash",
    ],
    KIND_UI_TWEAK: [
        "change color", "update copy", "move button", "restyle", "tweak padding",
        "rename label", "reposition", "font", "typography", "icon", "margin",
        "padding", "alignment", "redesign button",
        "doi mau", "doi copy", "doi chu", "can lai", "spacing", "doi font",
        "doi nut", "doi icon", "doi kich thuoc",
    ],
    KIND_REFACTOR: [
        "refactor", "clean up", "cleanup", "extract", "rename module",
        "migrate pattern", "split file", "reduce coupling", "reorganize",
        "toi uu code", "tai cau truc", "don code", "tach file", "doi cau truc",
    ],
    KIND_INVESTIGATION: [
        "how does", "how do", "why is", "why does", "what is", "can we",
        "is it possible", "explain", "audit", "analyze", "analyse", "compare",
        "feasibility", "research", "investigate",
        # documentation / spec review
        "review doc", "review docs", "review documentation", "check doc",
        "check docs", "verify spec", "verify docs", "inspect doc",
        # vietnamese variants
        "tai sao", "nhu the nao", "co the", "giai thich", "tim hieu", "khao sat",
        "kiem tra tai lieu", "kiem tra spec", "kiem tra doc",
        "ra soat", "ra soat tai lieu", "danh gia tai lieu",
        "doc tai lieu", "xem tai lieu", "phan tich tai lieu",
    ],
    KIND_FEATURE: [
        # English triggers — "add <something>", "build <something>", etc.
        "add ", "build ", "implement", "integrate", "support",
        "launch", "new flow", "new module", "new feature",
        # Vietnamese (no-diacritic) triggers
        "them tinh nang", "them chuc nang", "xay dung", "trien khai",
        "tich hop", "ho tro", "tinh nang moi", "module moi",
    ],
}


def _strip_diacritics(text: str) -> str:
    """Lowercase + remove Vietnamese diacritics so keywords can be ASCII-only."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.replace("đ", "d").replace("Đ", "d")

# Kind precedence when multiple match. bug_fix wins first (most specific).
# Feature ranks ABOVE ui_tweak/refactor so "add new login feature with red
# button" is routed as feature rather than ui_tweak.
_PRECEDENCE = [KIND_BUG_FIX, KIND_INVESTIGATION, KIND_FEATURE,
               KIND_UI_TWEAK, KIND_REFACTOR]


@dataclass
class RouteDecision:
    kind: str
    confidence: float
    reason: str
    sub_tasks: list[dict] = field(default_factory=list)   # [{kind, desc}]
    source: str = "heuristic"   # "heuristic" | "llm"
    raw: str = ""
    dynamic_steps: list[str] = field(default_factory=list)  # PM-decided steps

    @property
    def is_clear(self) -> bool:
        return self.confidence >= 0.6 and self.kind in ALL_KINDS

    def dispatch_steps(self) -> list[str]:
        if self.dynamic_steps:
            return list(self.dynamic_steps)
        # Investigation has its own minimal flow; everything else falls back
        # to the full step set so nothing important gets silently skipped
        # when PM didn't emit dynamic_steps for any reason.
        if self.kind == KIND_INVESTIGATION:
            return list(INVESTIGATION_STEPS)
        return list(ALL_STEPS)

    def to_markdown(self) -> str:
        lines = [
            "# PM Routing Decision",
            "",
            f"- **Kind**: `{self.kind}`",
            f"- **Confidence**: {self.confidence:.2f}",
            f"- **Source**: {self.source}",
            f"- **Dispatch**: {' → '.join(self.dispatch_steps())}",
            "",
            "## Reason",
            self.reason.strip() or "(no reason given)",
            "",
            "## Sub-tasks",
        ]
        if not self.sub_tasks:
            lines.append("- [NONE]")
        else:
            for st in self.sub_tasks:
                lines.append(f"- KIND: {st['kind']} | {st['desc']}")
        return "\n".join(lines)


class PMAgent(BaseAgent):
    ROLE = "Project Manager (PM)"
    RULE_KEY = "pm"
    SKILL_KEY = "pm"

    # ── Public API ────────────────────────────────────────────────────────────

    # Kinds whose dispatch plan rarely needs LLM tuning — heuristic + default
    # plan is good enough. When a keyword match is unambiguous (confidence
    # ≥ 0.85) for one of these kinds, we skip `_llm_decide_steps` entirely
    # and save ~800 tokens / task.
    def classify(self, request: str) -> RouteDecision:
        """Return a RouteDecision. Tries heuristic first, falls back to LLM."""
        heur = self._heuristic(request)
        if heur is not None and heur.confidence >= 0.85:
            decision = heur
        else:
            llm = self._llm_classify(request)
            if llm is None:
                decision = heur or RouteDecision(
                    kind=KIND_FEATURE,
                    confidence=0.4,
                    reason="Fallback: could not classify reliably, defaulting to feature.",
                    source="default",
                )
            else:
                decision = llm

        # Investigation has a fixed single-step flow — no need to ask the
        # LLM. Everything else first tries to read STEPS from whichever PM
        # routing skill the LLM self-picked during classify() (the MODE:
        # tag in the LLM reply was parsed by _record_mode_from_output and
        # populated `_active_skills`). If the picked skill declares STEPS
        # in its frontmatter, we use that — this skips an LLM round-trip.
        # Falls through to _llm_decide_steps (LLM picks per-task) only for
        # the `default` skill or when no skill matched.
        if decision.kind == KIND_INVESTIGATION:
            decision.dynamic_steps = list(INVESTIGATION_STEPS)
        else:
            skill_steps = self._steps_from_active_skill()
            if skill_steps:
                decision.dynamic_steps = skill_steps
                tprint(f"  📋 [PM] steps from skill `{self._active_skills[0]['skill_key']}` → "
                       f"{', '.join(skill_steps)}")
            else:
                decision.dynamic_steps = self._llm_decide_steps(request, decision.kind)
        return decision

    def _steps_from_active_skill(self) -> list[str]:
        """Return STEPS declared by the active PM routing skill, or [] if
        either no skill is active or the active skill doesn't declare STEPS
        (e.g. the `default` skill defers to the LLM step picker)."""
        if not self._active_skills:
            return []
        steps = self._active_skills[0].get("steps") or []
        # Validate: every entry must be a known step key.
        return [s for s in steps if s in ALL_STEPS]

    def _llm_decide_steps(self, request: str, kind: str) -> list[str]:
        """Ask PM (LLM) to reason about which pipeline steps the task
        actually needs. The LLM must distinguish:

          • **Code-producing** tasks → include `dev` + `test`
          • **Doc-only / spec-only** tasks → include `ba` (writes docs)
            and possibly `techlead` (writes architecture docs); SKIP
            `dev` + `test`
          • **Discovery / research** tasks → already routed to
            investigation kind; not handled here
          • **UI tweak only** → may skip `ba` and `techlead`

        We trust the LLM's judgement — there is no keyword fallback.
        On parse failure we pick the conservative full set, so a
        partially-broken LLM call doesn't accidentally drop QA.
        """
        prompt = f"""You are a Project Manager. Choose the MINIMUM set of pipeline steps needed for this specific task.

Available steps (run in this order if selected):
- ba: Requirements analysis — writes `docs/requirements/<feature>.md` + task list
- design: UI/UX design — writes design specs, screens, design system updates
- techlead: Architecture decisions — writes `docs/arch/<feature>.md`, sprint plan
- test_plan: Test planning — writes test scenarios + acceptance criteria
- dev: Implementation — writes/edits source code
- test: QA review — runs/writes test code

CRITICAL routing rules:
1. If the task is to **write/build/update/review documentation** (e.g. "viết tài liệu OAuth", "build API doc", "spec cho module X") → STEPS: ba   (and `techlead` only if architecture-level doc)
   ❌ Do NOT include `dev` or `test` — there is no code to write.
2. If the task is to **fix a bug** → STEPS: dev, test
3. If the task is **UI tweak only** (color/copy/spacing) → STEPS: design, dev, test
4. If the task is a **new feature** with code → STEPS: ba, design, techlead, test_plan, dev, test  (drop `design` if no UI)
5. If unclear which → include `dev` to be safe, but explain in REASON.

Task kind: {kind}
Request:
{request.strip()[:1000]}

Reply EXACTLY in this format (one line):
STEPS: <comma-separated>
REASON: <one short sentence why dev/test are or aren't included>"""

        try:
            raw = self._call(self.system_prompt, prompt)
            m = re.search(r"STEPS:\s*([a-z_,\s]+)", raw, re.IGNORECASE)
            if m:
                steps = [s.strip() for s in m.group(1).split(",")
                         if s.strip() in ALL_STEPS]
                if steps:
                    # Print the LLM's REASON so the user sees why steps were
                    # picked — especially important when `dev` is omitted.
                    rmatch = re.search(r"REASON:\s*(.+?)(?:\n|$)", raw, re.IGNORECASE)
                    reason = rmatch.group(1).strip() if rmatch else ""
                    if "dev" not in steps:
                        tprint(f"  ⚠️  [PM] no `dev` step. Reason: {reason or '(none)'}")
                    elif reason:
                        tprint(f"  ℹ️  [PM] steps reason: {reason}")
                    return steps
        except Exception as e:
            tprint(f"  ⚠️  [PM] dynamic steps failed: {e}")
        # Conservative fallback only if LLM call totally broke (timeout,
        # malformed reply). Better to run an extra step than silently
        # drop QA on a real code change.
        return list(ALL_STEPS)

    def dispatch_plan(self, kind: str) -> list[str]:
        """Default dispatch plan for a kind. Kept for back-compat callers
        — internal code now routes through `_llm_decide_steps`."""
        if kind == KIND_INVESTIGATION:
            return list(INVESTIGATION_STEPS)
        return list(ALL_STEPS)

    # ── Heuristic layer ───────────────────────────────────────────────────────

    def _heuristic(self, request: str) -> RouteDecision | None:
        text = _strip_diacritics(request)
        hits: dict[str, list[str]] = {k: [] for k in ALL_KINDS}
        for kind, kws in _KEYWORDS.items():
            for kw in kws:
                if kw in text:
                    hits[kind].append(kw)

        matched_kinds = [k for k in ALL_KINDS if hits[k]]
        if not matched_kinds:
            return None

        # Resolve ties via precedence.
        for kind in _PRECEDENCE:
            if kind in matched_kinds:
                chosen = kind
                break
        else:
            chosen = matched_kinds[0]

        # Confidence = 0.85 when single category matched, 0.65 when ≥2 categories tied.
        confidence = 0.85 if len(matched_kinds) == 1 else 0.65
        reason = (
            f"Matched keywords for `{chosen}`: "
            + ", ".join(f"'{kw}'" for kw in hits[chosen][:4])
        )
        if len(matched_kinds) > 1:
            reason += (
                f". Other candidates detected via keyword: "
                + ", ".join(f"{k}({len(hits[k])})"
                            for k in matched_kinds if k != chosen)
            )
        return RouteDecision(
            kind=chosen,
            confidence=confidence,
            reason=reason,
            source="heuristic",
        )

    # ── LLM layer ─────────────────────────────────────────────────────────────

    def _llm_classify(self, request: str) -> RouteDecision | None:
        prompt = (
            "Classify the following request per the rules. "
            "Reply ONLY with the required format — no prose.\n\n"
            "=== REQUEST ===\n"
            f"{request.strip()}\n"
        )
        try:
            raw = self._call(self.system_prompt, prompt)
        except Exception as e:
            tprint(f"  ⚠️  [PM] LLM classify failed: {e}")
            return None

        decision = self._parse_output(raw)
        if decision is None:
            return None
        decision.source = "llm"
        decision.raw = raw
        return decision

    # ── Parser ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_output(raw: str) -> RouteDecision | None:
        """Parse the KIND/CONFIDENCE/REASON/SUB_TASKS block from LLM output."""
        if not raw:
            return None

        kind_match = re.search(r"^KIND:\s*([a-zA-Z_]+)", raw, re.MULTILINE)
        if not kind_match:
            return None
        kind = kind_match.group(1).strip().lower()
        if kind not in ALL_KINDS:
            # Common alias cleanup.
            aliases = {
                "bugfix": KIND_BUG_FIX,
                "bug": KIND_BUG_FIX,
                "ui": KIND_UI_TWEAK,
                "ui-tweak": KIND_UI_TWEAK,
                "style": KIND_UI_TWEAK,
                "qa": KIND_INVESTIGATION,
                "research": KIND_INVESTIGATION,
            }
            kind = aliases.get(kind, kind)
            if kind not in ALL_KINDS:
                return None

        conf_match = re.search(r"^CONFIDENCE:\s*([0-9]*\.?[0-9]+)", raw, re.MULTILINE)
        try:
            confidence = float(conf_match.group(1)) if conf_match else 0.5
        except ValueError:
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        reason_match = re.search(
            r"^REASON:\s*(.+?)(?:\n\s*\n|\nSUB_TASKS:|\Z)",
            raw, re.MULTILINE | re.DOTALL,
        )
        reason = (reason_match.group(1).strip() if reason_match else "").strip()

        sub_tasks: list[dict] = []
        sub_block = re.search(
            r"SUB_TASKS:\s*\n(.+?)(?:\n\s*\n|\Z)",
            raw, re.MULTILINE | re.DOTALL,
        )
        if sub_block:
            for line in sub_block.group(1).splitlines():
                line = line.strip().lstrip("-").strip()
                if not line or "[none]" in line.lower():
                    continue
                m = re.match(r"KIND:\s*([a-zA-Z_]+)\s*\|\s*(.+)", line, re.IGNORECASE)
                if m:
                    sk = m.group(1).strip().lower()
                    if sk in ALL_KINDS:
                        sub_tasks.append({"kind": sk, "desc": m.group(2).strip()})

        return RouteDecision(
            kind=kind,
            confidence=confidence,
            reason=reason,
            sub_tasks=sub_tasks,
        )
