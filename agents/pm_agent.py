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

# Sub-pipeline per kind. Step keys match orchestrator.STEP_KEYS.
DISPATCH_PLAN: dict[str, list[str]] = {
    # BA is INCLUDED only for `feature` (where real requirement analysis is
    # the value-add). For the lean kinds it is optional — the orchestrator
    # synthesises a placeholder Task from the raw request so downstream
    # Dev/Test still have structured input without spending BA tokens.
    KIND_FEATURE:       ["ba", "design", "techlead", "test_plan", "dev", "test"],
    KIND_BUG_FIX:       ["dev", "test"],
    KIND_UI_TWEAK:      ["design", "dev", "test"],
    KIND_REFACTOR:      ["techlead", "dev", "test"],
    KIND_INVESTIGATION: ["investigation"],
}

# Kinds for which BA can be re-added if user passes --with-ba or per-profile
# override. (Hook for future extension; not wired to CLI yet.)
BA_OPTIONAL_KINDS = (KIND_BUG_FIX, KIND_UI_TWEAK, KIND_REFACTOR)

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
        "tai sao", "nhu the nao", "co the", "giai thich", "tim hieu", "khao sat",
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

    @property
    def is_clear(self) -> bool:
        return self.confidence >= 0.6 and self.kind in ALL_KINDS

    def dispatch_steps(self) -> list[str]:
        return list(DISPATCH_PLAN.get(self.kind, DISPATCH_PLAN[KIND_FEATURE]))

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

    def classify(self, request: str) -> RouteDecision:
        """Return a RouteDecision. Tries heuristic first, falls back to LLM."""
        heur = self._heuristic(request)
        if heur is not None and heur.confidence >= 0.85:
            return heur

        # Heuristic was ambiguous or empty — ask the LLM.
        llm = self._llm_classify(request)
        if llm is None:
            # LLM failed — fall back to whatever heuristic gave us, or default.
            if heur is not None:
                return heur
            return RouteDecision(
                kind=KIND_FEATURE,
                confidence=0.4,
                reason="Fallback: could not classify reliably, defaulting to feature.",
                source="default",
            )
        return llm

    def dispatch_plan(self, kind: str) -> list[str]:
        """Expose DISPATCH_PLAN for the orchestrator."""
        return list(DISPATCH_PLAN.get(kind, DISPATCH_PLAN[KIND_FEATURE]))

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
            raw = self._call(self.system_prompt, prompt, max_tokens=500)
        except Exception as e:
            print(f"  ⚠️  [PM] LLM classify failed: {e}")
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
