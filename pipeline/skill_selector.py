"""
Skill Selector — auto-picks the best skill for each agent based on task scope.

Architecture:
  skills/<agent_key>/
    _detect.md      # optional: extra hints for scope detection
    <skill>.md      # each skill = specialized system prompt + scope tags

Scope ladder (from feature → full app):
  simple   : 1 màn, no state phức tạp
  feature  : 1 feature (nhiều màn, 1 module)
  module   : nhiều feature in 1 domain
  full_app : toàn bộ app (nhiều module, navigation, CI/CD)
  bug_fix  : maintain — chỉ fix code current có
"""
from __future__ import annotations
import re
from pathlib import Path

from core.paths import SKILLS_DIR as _SKILLS_DIR

SCOPES = ["simple", "feature", "module", "full_app", "bug_fix"]

# Keyword → scope mapping for heuristic detection (fast, no LLM)
_SCOPE_KEYWORDS: dict[str, list[str]] = {
    "bug_fix":  ["fix", "bug", "crash", "regression", "not working",
                 "doesn't work", "broken", "error in", "hotfix", "patch"],
    "full_app": ["full app", "toàn bộ app", "entire app", "end-to-end product",
                 "mvp", "platform", "app new", "new app", "ecosystem", "suite",
                 "nhiều module", "multi-module", "marketplace", "super app"],
    "module":   ["module", "domain", "cả phần", "entire feature set", "mini-app",
                 "dashboard", "admin panel", "onboarding flow", "full flow"],
    "feature":  ["feature", "tính năng", "chức năng", "flow", "feature mới",
                 "new feature", "user story", "sprint"],
    "simple":   ["1 màn", "màn hình", "1 screen", "simple", "quick", "prototype",
                 "widget", "popup", "dialog", "component"],
}

# Per-scope defaults when no sin signal found
_DEFAULT_SCOPE = "feature"


def detect_scope(task: str, project_context: str = "") -> str:
    """
    Heuristic scope detection. Returns one of SCOPES.
    Priority: bug_fix > full_app > module > feature > simple.
    """
    text = (task + " " + project_context[:2000]).lower()

    def _kw_count(kw: str, hay: str) -> int:
        # Word-boundary match so "fix" doesn't hit "prefix/fixture",
        # "bug" doesn't hit "debug", etc. (Unicode \w covers Vietnamese.)
        return len(re.findall(rf"(?<!\w){re.escape(kw)}(?!\w)", hay))

    # Bug_fix wins immediately if any keyword matches IN THE TASK itself
    # (project_context hits are too noisy — code comments mention "fix"/"bug"
    # all the time without the user intending a bug-fix scope).
    task_lower = task.lower()
    for kw in _SCOPE_KEYWORDS["bug_fix"]:
        if _kw_count(kw, task_lower) > 0:
            return "bug_fix"

    # Otherwise, score each scope by keyword density
    scores: dict[str, int] = {s: 0 for s in SCOPES}
    for scope, keywords in _SCOPE_KEYWORDS.items():
        if scope == "bug_fix":
            continue
        for kw in keywords:
            scores[scope] += _kw_count(kw, text) * (len(kw.split()) + 1)

    # File count hint from project context
    file_count = len(re.findall(r"^\s*###\s+", project_context, re.MULTILINE))
    if file_count > 40:
        scores["full_app"] += 3
    elif file_count > 15:
        scores["module"] += 2

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else _DEFAULT_SCOPE


def list_skills(agent_key: str) -> list[dict]:
    """Return metadata for every skill available to this agent.

    Scans `skills/<agent>/**/*.md` recursively so skills can be organised
    into category subfolders (scope/, domain/, phase/, …). The skill_key
    is still the file stem — duplicates across subfolders are not allowed
    and the first hit wins (deterministic via sorted scan).
    """
    agent_dir = _SKILLS_DIR / agent_key
    if not agent_dir.exists():
        return []

    skills: list[dict] = []
    seen: set[str] = set()
    # Subfolder paths (longer parts) sort AFTER flat files alphabetically, so
    # walk depth-first to give subfolder versions precedence over root-level
    # shims when both exist.
    paths = sorted(agent_dir.rglob("*.md"), key=lambda p: (len(p.parts), str(p)))
    paths.sort(key=lambda p: -len(p.parts))   # deeper first
    for path in paths:
        if path.stem.startswith("_"):  # _detect.md, _registry.md…
            continue
        # Skip "moved" shims left behind when we can't unlink files.
        try:
            head = path.read_text(encoding="utf-8")[:300]
            if "MOVED_TO:" in head:
                continue
        except OSError:
            continue
        if path.stem in seen:
            continue   # first hit (deeper path) wins
        seen.add(path.stem)
        meta = _parse_skill_meta(path)
        meta["path"] = path
        meta["skill_key"] = path.stem
        rel = path.relative_to(agent_dir).parent
        meta["category"] = "" if str(rel) == "." else str(rel).replace("/", "·")
        skills.append(meta)
    # Final sort: by category then key for stable menu ordering
    skills.sort(key=lambda s: (s["category"], s["skill_key"]))
    return skills


def _parse_skill_meta(path: Path) -> dict:
    """Parse skill frontmatter: SCOPE, TRIGGERS, MAX_TOKENS, DEPENDS_ON, STEPS.

    `STEPS` is optional — used by PM routing skills to declare the pipeline
    sub-set this skill dispatches. When present, PMAgent uses it directly
    and skips the LLM step picker (saves an LLM call per session).
    """
    content = path.read_text(encoding="utf-8")
    meta: dict = {
        "scope":      [],
        "triggers":   [],
        "max_tokens": 4096,
        "depends_on": [],
        "steps":      [],
        "content":    content,
    }

    # Look for frontmatter block at top
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
    if m:
        header, body = m.group(1), m.group(2)
        meta["content"] = body.strip()
    else:
        header = content[:500]  # scan first 500 chars for inline metadata

    for line in header.splitlines():
        s = line.strip()
        if s.upper().startswith("SCOPE:"):
            meta["scope"] = [v.strip().lower()
                             for v in s.split(":", 1)[1].split(",") if v.strip()]
        elif s.upper().startswith("TRIGGERS:"):
            meta["triggers"] = [v.strip().lower()
                                for v in s.split(":", 1)[1].split(",") if v.strip()]
        elif s.upper().startswith("MAX_TOKENS:"):
            try:
                meta["max_tokens"] = int(s.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif s.upper().startswith("DEPENDS_ON:"):
            meta["depends_on"] = [v.strip().lower()
                                   for v in s.split(":", 1)[1].split(",") if v.strip()]
        elif s.upper().startswith("STEPS:"):
            meta["steps"] = [v.strip().lower()
                              for v in s.split(":", 1)[1].split(",") if v.strip()]

    return meta


def select_skill(agent_key: str, task: str, project_context: str = "",
                 scope_hint: str | None = None, llm_fallback=None) -> dict | None:
    """
    Pick the best skill for this agent + task.
    1. Detect scope from task
    2. Filter skills matching scope
    3. Score remaining by trigger keyword matches
    4. If ambiguous (top-2 within 1 point) → llm_fallback callable resolves
    5. Return highest-scoring skill (or None if nothing matches)
    """
    scope   = scope_hint or detect_scope(task, project_context)
    all_skills = list_skills(agent_key)

    if not all_skills:
        return None

    # Filter by scope
    matching = [s for s in all_skills if scope in s["scope"] or not s["scope"]]
    if not matching:
        matching = all_skills  # fallback — no scope match, consider all

    # Score by trigger keyword overlap
    text = task.lower() + " " + project_context[:2000].lower()
    scored: list[tuple[int, dict]] = []
    for skill in matching:
        score = 0
        for trigger in skill["triggers"]:
            if trigger in text:
                score += len(trigger.split()) + 1
        if scope in skill["scope"]:
            score += 2
        scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Ambiguous when top-2 are within 1 point AND callback available
    if llm_fallback and len(scored) >= 2 and (scored[0][0] - scored[1][0]) <= 1:
        candidates = [s for _, s in scored[:3]]
        chosen_key = llm_fallback(agent_key, task, candidates)
        for _, skill in scored:
            if skill["skill_key"] == chosen_key:
                skill["detected_scope"] = scope
                skill["selection_method"] = "llm"
                return skill

    chosen = scored[0][1] if scored else matching[0]
    chosen["detected_scope"] = scope
    chosen["selection_method"] = "keyword"
    return chosen


def llm_pick_skill(call_fn, agent_key: str, task: str, candidates: list[dict]) -> str:
    """
    When heuristics are ambiguous, ask the model directly.
    call_fn(system, user, max_tokens) -> str — same signature as BaseAgent._call.
    Returns the chosen skill_key.
    """
    options = "\n".join(
        f"- {s['skill_key']}: scope={s['scope']}  triggers={s['triggers'][:4]}"
        for s in candidates
    )
    system = (
        f"You are the {agent_key} agent. Choose the most suitable skill for this task. "
        "Return only the skill_key name, no explanation."
    )
    user = f"Task:\n{task[:600]}\n\nSkills có sẵn:\n{options}\n\nSkill_key nào?"
    try:
        raw = call_fn(system, user, max_tokens=50)
        for s in candidates:
            if s["skill_key"] in raw:
                return s["skill_key"]
    except (ValueError, KeyError, AttributeError, TypeError, IndexError):
        pass
    return candidates[0]["skill_key"]


def format_metadata_summary(meta: dict | None) -> str:
    """Render a compact metadata block for LLM prompts. Empty string if none."""
    if not meta:
        return ""
    lines = []
    if meta.get("scopes"):
        lines.append(f"- scope: {', '.join(meta['scopes'])}")
    if meta.get("max_risk"):
        lines.append(f"- max risk_level: {meta['max_risk']}")
    if meta.get("max_complexity"):
        lines.append(f"- max complexity: {meta['max_complexity']}")
    if meta.get("impact_area"):
        lines.append(f"- impact_area: {', '.join(meta['impact_area'])}")
    if meta.get("integrity_blacklist_hits"):
        lines.append(
            "- integrity alerts (force strict skill): "
            f"{', '.join(meta['integrity_blacklist_hits'])}"
        )
    if meta.get("emergency_audit"):
        lines.append("- session state: EMERGENCY_AUDIT active (prior skipped-Critic failed QA)")
    if meta.get("hotfix_p0"):
        lines.append("- hotfix+P0 present → live incident mode")
    if not lines:
        return ""
    return "\n## Task metadata (decision signals)\n" + "\n".join(lines)


def llm_pick_skills_multi(call_fn, agent_key: str, task: str,
                           candidates: list[dict], max_n: int = 2,
                           task_metadata: dict | None = None) -> list[str]:
    """
    Ask Claude to pick 1..max_n complementary skills. Used by LLM-auto mode
    (env MULTI_AGENT_SKILL_LLM=1). Returns skill_keys in priority order.

    Strategy: tell the model to pick at most `max_n` and prefer picking 1
    unless the task genuinely spans two orthogonal concerns (e.g. stack +
    domain). Fallback: first candidate.

    When `task_metadata` is provided (typically for TL/Design/Dev/Test
    steps that run after BA has emitted metadata), a compact summary is
    injected into the prompt so the model can route on semantic signals
    (scope, risk, impact_area, integrity alerts) instead of keyword text.
    """
    options = "\n".join(
        f"- {s['skill_key']}: scope={s['scope']}  triggers={', '.join(s['triggers'][:5])}"
        for s in candidates
    )
    meta_block = format_metadata_summary(task_metadata)
    system = (
        f"You là agent {agent_key}. Với task sau, chọn TỪ 1 ĐẾN {max_n} skill "
        f"để kết hợp. Chỉ chọn >1 khi task thực sự chạm nhiều concern độc lập "
        f"(stack + domain, stack + mode, v.v.).\n"
        f"Khi có Task metadata: ưu tiên skill khớp scope/risk/impact_area; "
        f"nếu có integrity alert cho 1 module → bắt buộc pick skill defensive "
        f"(ecommerce/hotfix_emergency/integration_api...).\n"
        f"Trả lời gọn:\nSKILLS: skill_key_1, skill_key_2"
    )
    user = f"Task:\n{task[:600]}\n{meta_block}\n\nSkills có sẵn:\n{options}"
    try:
        raw = call_fn(system, user, max_tokens=120)
        m = re.search(r"SKILLS:\s*(.+)", raw, re.IGNORECASE)
        names_line = m.group(1) if m else raw
        picked: list[str] = []
        valid = {s["skill_key"] for s in candidates}
        for part in re.split(r"[,;\n]", names_line):
            key = part.strip().strip("`").strip("'\"")
            if key in valid and key not in picked:
                picked.append(key)
            if len(picked) >= max_n:
                break
        if picked:
            return picked
    except (ValueError, KeyError, AttributeError, TypeError, IndexError):
        pass
    return [candidates[0]["skill_key"]] if candidates else []


# ── Multi-skill selection ────────────────────────────────────────────────────

def select_skills(agent_key: str, task: str, project_context: str = "",
                   scope_hint: str | None = None, llm_fallback=None,
                   max_n: int = 2, llm_auto: bool = False,
                   task_metadata: dict | None = None) -> list[dict]:
    """
    Return 1..max_n skills to activate for this agent+task.

    Modes:
      llm_auto=False (default): heuristic scoring; secondary kept only if
        score ≥ 70% of primary.
      llm_auto=True: always ask Claude to pick 1..max_n skills (requires
        llm_fallback to be a call function, not None).
    """
    scope = scope_hint or detect_scope(task, project_context)
    all_skills = list_skills(agent_key)
    if not all_skills:
        return []

    matching = [s for s in all_skills if scope in s["scope"] or not s["scope"]]
    if not matching:
        matching = all_skills

    text = task.lower() + " " + project_context[:2000].lower()
    scored: list[tuple[int, dict]] = []
    for skill in matching:
        score = 0
        for trigger in skill["triggers"]:
            if trigger in text:
                score += len(trigger.split()) + 1
        if scope in skill["scope"]:
            score += 2
        scored.append((score, skill))
    scored.sort(key=lambda x: x[0], reverse=True)

    # ── LLM-auto mode: hand the top candidates to Claude and honour its pick
    if llm_auto and llm_fallback and len(scored) >= 1:
        candidates = [s for _, s in scored[: max(max_n + 1, 3)]]
        picked_keys = llm_fallback("multi", agent_key, task, candidates,
                                    max_n, task_metadata)
        picked: list[dict] = []
        for key in picked_keys:
            for _, s in scored:
                if s["skill_key"] == key and s not in picked:
                    s["detected_scope"]   = scope
                    s["selection_method"] = "llm_auto"
                    s["rank"]             = len(picked) + 1
                    picked.append(s)
                    break
        if picked:
            return picked

    # ── Heuristic mode — primary + optional secondary
    if not scored:
        return []
    primary_score, primary = scored[0]
    primary["detected_scope"]   = scope
    primary["selection_method"] = "keyword"
    primary["rank"]             = 1
    result = [primary]

    if max_n >= 2 and len(scored) >= 2 and primary_score > 0:
        second_score, second = scored[1]
        if second_score >= primary_score * 0.7 and second["skill_key"] != primary["skill_key"]:
            second["detected_scope"]   = scope
            second["selection_method"] = "keyword_secondary"
            second["rank"]             = 2
            result.append(second)

    return result


def render_skill(skill: dict, base_rule: str) -> str:
    """Combine base rule + skill-specific instructions into final system prompt."""
    if not skill:
        return base_rule
    return (
        f"{base_rule}\n\n"
        f"---\n\n"
        f"## 🎯 ACTIVE SKILL: {skill['skill_key']} (scope: {skill.get('detected_scope', '?')})\n\n"
        f"{skill['content']}"
    )


def render_skills(skills: list[dict], base_rule: str) -> str:
    """Merge 1..N skills into a single system prompt. Order = priority rank.

    When multiple skills are given, each one gets its own labelled section
    and a header notes the combination so the model can reason about
    potential conflicts.
    """
    if not skills:
        return base_rule
    if len(skills) == 1:
        return render_skill(skills[0], base_rule)

    keys = " + ".join(s["skill_key"] for s in skills)
    parts = [
        base_rule,
        "",
        "---",
        "",
        f"## 🎯 ACTIVE SKILLS (combined): {keys}",
        "",
        "When skill guidance conflicts, the PRIMARY skill takes precedence "
        "over secondary ones. Apply all non-conflicting rules from every "
        "active skill.",
        "",
    ]
    for idx, s in enumerate(skills, 1):
        label = "PRIMARY" if idx == 1 else f"SECONDARY #{idx-1}"
        parts += [
            f"### {label} — {s['skill_key']} (scope: {s.get('detected_scope','?')})",
            "",
            s["content"],
            "",
        ]
    return "\n".join(parts)
