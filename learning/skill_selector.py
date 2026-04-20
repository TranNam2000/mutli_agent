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

_SKILLS_DIR = Path(__file__).parent.parent / "skills"

SCOPES = ["simple", "feature", "module", "full_app", "bug_fix"]

# Keyword → scope mapping for heuristic detection (fast, no LLM)
_SCOPE_KEYWORDS: dict[str, list[str]] = {
    "bug_fix":  ["fix", "bug", "fix", "bug", "crash", "regression", "not working",
                 "doesn't work", "broken", "error in", "hotfix", "patch"],
    "full_app": ["full app", "toàn bộ app", "entire app", "end-to-end product",
                 "mvp", "platform", "app new", "new app", "ecosystem", "suite",
                 "nhiều module", "multi-module", "marketplace", "super app"],
    "module":   ["module", "domain", "cả phần", "entire feature set", "mini-app",
                 "dashboard", "admin panel", "onboarding flow", "full flow"],
    "feature":  ["feature", "tính năng", "chức năng", "flow", "feature new",
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

    # Bug_fix wins immediately if any keyword matches
    for kw in _SCOPE_KEYWORDS["bug_fix"]:
        if kw in text:
            return "bug_fix"

    # Otherwise, score each scope by keyword density
    scores: dict[str, int] = {s: 0 for s in SCOPES}
    for scope, keywords in _SCOPE_KEYWORDS.items():
        if scope == "bug_fix":
            continue
        for kw in keywords:
            scores[scope] += text.count(kw) * (len(kw.split()) + 1)

    # File count hint from project context
    file_count = len(re.findall(r"^\s*###\s+", project_context, re.MULTILINE))
    if file_count > 40:
        scores["full_app"] += 3
    elif file_count > 15:
        scores["module"] += 2

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else _DEFAULT_SCOPE


def list_skills(agent_key: str) -> list[dict]:
    """Return metadata for every skill available to this agent."""
    agent_dir = _SKILLS_DIR / agent_key
    if not agent_dir.exists():
        return []

    skills = []
    for path in sorted(agent_dir.glob("*.md")):
        if path.stem.startswith("_"):  # _detect.md, _registry.md…
            continue
        meta = _parse_skill_meta(path)
        meta["path"] = path
        meta["skill_key"] = path.stem
        skills.append(meta)
    return skills


def _parse_skill_meta(path: Path) -> dict:
    """Parse skill frontmatter: SCOPE, TRIGGERS, MAX_TOKENS, DEPENDS_ON."""
    content = path.read_text(encoding="utf-8")
    meta: dict = {
        "scope":      [],
        "triggers":   [],
        "max_tokens": 4096,
        "depends_on": [],
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
    Returns the forsen skill_key.
    """
    options = "\n".join(
        f"- {s['skill_key']}: scope={s['scope']}  triggers={s['triggers'][:4]}"
        for s in candidates
    )
    system = (
        f"You is {agent_key} agent. Choose skill phù hợp nhất for task. "
        "Chỉ trả về name skill_key, no giải thích."
    )
    user = f"Task:\n{task[:600]}\n\nSkills có sẵn:\n{options}\n\nSkill_key nào?"
    try:
        raw = call_fn(system, user, max_tokens=50)
        for s in candidates:
            if s["skill_key"] in raw:
                return s["skill_key"]
    except Exception:
        pass
    return candidates[0]["skill_key"]


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
