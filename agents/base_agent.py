"""Base agent using Claude Code CLI. Auth is whatever `claude` is configured
with locally — this module does not touch env vars or API keys."""
from __future__ import annotations
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from core.paths import RULES_DIR as _RULES_DIR
from core.config import get_bool, get_int
from core.exceptions import AgentCallError
from core.logging import tprint

if TYPE_CHECKING:
    from core.message_bus import MessageBus
    from core.token_tracker import TokenTracker

# Global semaphore: cap concurrent Claude CLI calls to avoid quota spike.
# Default 3 — adjustable via env var.
_MAX_CONCURRENT = get_int("MULTI_AGENT_MAX_CONCURRENT", min_value=1, max_value=16)
_CALL_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT)

# Minimum spacing between call starts (ms) — avoids burst rate limit.
_CALL_MIN_SPACING_MS = get_int("MULTI_AGENT_CALL_SPACING_MS", min_value=0)
_LAST_CALL_TIME = [0.0]
_SPACING_LOCK = threading.Lock()


def _load_rule(name: str, profile: str = "default") -> str:
    """Load rule from rules/<profile>/<name>.md, fallback to rules/default/<name>.md."""
    for p in [profile, "default"]:
        path = _RULES_DIR / p / f"{name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(
        f"Rule file not found: '{name}' in profile '{profile}' or 'default'. "
        f"Expected at: {_RULES_DIR / 'default' / name}.md"
    )


# ── Skills-as-menu (self-routed by LLM) ─────────────────────────────────────

_SKILL_SUMMARY_CHARS = 280   # soft cap per skill body in the menu


def _summarise_skill(content: str) -> str:
    """Strip frontmatter + tool-use hint + take the first prose section
    (≤ _SKILL_SUMMARY_CHARS chars) so the WORKING MODES menu stays compact.

    Skills are markdown with optional `--- SCOPE/TRIGGERS frontmatter ---`,
    then a `# Title` heading, then prose. We drop frontmatter + the global
    `<!-- TOOL-USE-HINT v1 -->` block at the bottom.
    """
    body = content
    # Drop frontmatter
    m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)", body, re.DOTALL)
    if m:
        body = m.group(1)
    # Drop tool-use hint block (and everything after)
    body = re.split(r"<!--\s*TOOL-USE-HINT", body, maxsplit=1)[0]
    body = body.strip()
    # Drop the `# Title` line if present — agent already knows its role
    body = re.sub(r"^#\s+[^\n]*\n+", "", body, count=1)
    # Soft truncate
    if len(body) > _SKILL_SUMMARY_CHARS:
        body = body[:_SKILL_SUMMARY_CHARS].rstrip() + " …"
    return body


def _render_skills_menu(skills: list[dict]) -> str:
    """Build the `WORKING MODES` block listed below the base rule."""
    parts = [
        "---",
        "",
        "## WORKING MODES",
        "",
        "Multiple specialised modes are available below. Pick the ONE that "
        "best fits the task and apply its style + checklist. If two modes "
        "overlap, pick the more specific one. To make the choice auditable, "
        "start your reply with `MODE: <name>` (single line).",
        "",
    ]
    for s in skills:
        triggers = ", ".join(s.get("triggers", [])[:6]) or "—"
        scope = ", ".join(s.get("scope") or []) or "any"
        parts += [
            f"### `{s['skill_key']}`",
            f"- scope: {scope}",
            f"- triggers: {triggers}",
            "",
            _summarise_skill(s.get("content", "")),
            "",
        ]
    return "\n".join(parts)


_MODE_TAG_RE = re.compile(r"(?im)^\s*MODE\s*:\s*([a-z0-9_\-]+)")


def _parse_mode_tag(output: str) -> str | None:
    """Pick out the `MODE: <skill>` line emitted by the LLM, if any."""
    if not output:
        return None
    m = _MODE_TAG_RE.search(output)
    return m.group(1).strip().lower() if m else None


class BaseAgent:
    ROLE = "Agent"
    RULE_KEY: str = ""  # subclasses override this to match rules/<profile>/<key>.md
    SKILL_KEY: str = ""  # subclasses override this to match skills/<key>/ folder

    def __init__(self, model: str = "claude-sonnet-4-6", profile: str = "default"):
        self.model = model
        self.profile = profile
        self.message_bus: MessageBus | None = None
        self.token_tracker: TokenTracker | None = None  # set by orchestrator
        self._system_prompt: str = ""
        self.project_context: str = ""  # set by orchestrator when --maintain
        # When set, claude CLI runs with cwd=project_root so its native
        # Read/Edit/Write/Bash tools act on the user's project. Set by the
        # orchestrator once it detects maintain mode + project root.
        self.cwd: str | None = None
        # Set by orchestrator so agents can call SkillDesigner to auto-create
        # a skill when no existing one matches the user's task.
        self._skill_designer: BaseAgent | None = None
        self._current_step: str = ""   # set by orchestrator before each step
        # Multi-skill support. `_active_skills` is the canonical state;
        # the legacy `_active_skill` attribute is a back-compat shim for any
        # caller that still reads the single-skill API.
        self._active_skills: list[dict] = []
        self._skill_usage_log: list[dict] = []  # for skill optimizer
        # Rule A/B variant: orchestrator sets to "shadow" when this session
        # should evaluate rules/<profile>/<agent>.shadow.md instead of the
        # baseline <agent>.md. Default "baseline".
        self._rule_variant: str = "baseline"
        # Cache for effective rule text — rules are immutable during a session,
        # so we can skip re-reading the markdown file on every `_call`.
        # Key: (RULE_KEY, profile, _rule_variant) → loaded text.
        self._rule_cache: dict[tuple[str, str, str], str] = {}

    @property
    def _active_skill(self) -> dict | None:
        """Back-compat alias — primary (rank=1) skill, or None."""
        return self._active_skills[0] if self._active_skills else None

    @_active_skill.setter
    def _active_skill(self, value: dict | None) -> None:
        self._active_skills = [value] if value else []

    def _load_effective_rule(self) -> str:
        """Load the rule file, honouring shadow A/B variant when active.

        When `_rule_variant == "shadow"` and `<RULE_KEY>.shadow.md` exists
        in the active profile, load that file instead of the baseline. The
        orchestrator flips the variant per session (session_id parity) so
        ShadowLog can compare the two side by side.

        Result cached per (RULE_KEY, profile, variant) — invalidate via
        `invalidate_rule_cache()` when the underlying file changes.
        """
        if not self.RULE_KEY:
            return self._system_prompt
        cache_key = (self.RULE_KEY, self.profile, self._rule_variant)
        cached = self._rule_cache.get(cache_key)
        if cached is not None:
            return cached

        if self._rule_variant == "shadow":
            for p in [self.profile, "default"]:
                shadow_path = _RULES_DIR / p / f"{self.RULE_KEY}.shadow.md"
                if shadow_path.exists():
                    text = shadow_path.read_text(encoding="utf-8").strip()
                    self._rule_cache[cache_key] = text
                    return text
        text = _load_rule(self.RULE_KEY, self.profile)
        self._rule_cache[cache_key] = text
        return text

    def invalidate_rule_cache(self) -> None:
        """Drop the cached rule text — call this if you edit rules mid-session."""
        self._rule_cache.clear()

    @property
    def system_prompt(self) -> str:
        """Load base rule, then append a `WORKING MODES` block listing every
        skill available to this agent (frontmatter + first section). The LLM
        picks the right mode itself during the main call and signals its
        choice with `MODE: <skill_key>` in the reply for audit.

        Removes the legacy two-step "Python picker → bake skill content"
        flow — skills are now self-served from a menu the LLM sees.

        Honours shadow A/B variant on the base rule.
        """
        base = self._load_effective_rule()
        if not self.SKILL_KEY:
            return base
        try:
            from pipeline.skill_selector import list_skills
            skills = list_skills(self.SKILL_KEY)
        except (ImportError, AttributeError, KeyError, OSError):
            return base
        if not skills:
            return base
        return base + "\n\n" + _render_skills_menu(skills)

    def detect_skill(self, task: str, scope_hint: str | None = None,
                      task_metadata: dict | None = None) -> dict | None:
        """No-op shim — kept for back-compat with callers that pre-warmed a
        skill choice.

        Skill picking is now self-served by the LLM during the main task
        call: every available skill appears as an entry in the WORKING
        MODES menu (see `system_prompt`), and the LLM signals its choice
        with `MODE: <skill_key>` in the reply. We parse that tag in
        `_call_with_retry` and populate `_active_skills` + `_skill_usage_log`
        post-hoc, so downstream analytics keep working.
        """
        return None

    def clear_skill(self):
        """Reset active skills (e.g. between independent tasks)."""
        self._active_skills = []

    def _auto_create_skill(self, task: str) -> dict | None:
        """When no existing skill matches the task, ask SkillDesigner to
        write a fresh one and reload it. Best-effort — silent on failure.

        Skill file lands in `skills/<SKILL_KEY>/auto/<slug>.md` so it's
        clearly tagged as auto-generated and can be reviewed/promoted by
        a human later.

        Gate via MULTI_AGENT_AUTO_CREATE_SKILL=0 to disable (default on).
        """
        # Default ON — opt out via MULTI_AGENT_AUTO_CREATE_SKILL=0
        if not get_bool("MULTI_AGENT_AUTO_CREATE_SKILL"):
            return None
        if not self.SKILL_KEY or not self._skill_designer:
            return None

        slug = re.sub(r"[^a-z0-9_]+", "_", (task or "").lower().strip())[:40]
        slug = slug.strip("_") or "auto_skill"

        try:
            from pipeline.skill_selector import list_skills
        except ImportError:
            return None
        # Don't duplicate an existing slug
        existing = {s["skill_key"] for s in list_skills(self.SKILL_KEY)}
        if slug in existing:
            slug = f"{slug}_{len(existing)}"

        tprint(f"  🛠 [{self.ROLE}] no skill matched — calling SkillDesigner "
               f"to draft `{slug}`...")
        try:
            result = self._skill_designer.design_new_skill(
                agent_key=self.SKILL_KEY,
                proposed_key=slug,
                pattern=f"No existing skill matched user request:\n{task[:200]}",
                task_sample=task[:600],
            )
        except Exception as e:
            tprint(f"  ⚠️  [{self.ROLE}] SkillDesigner call failed: {e}")
            return None
        if not result or not result.get("ok") or not result.get("content"):
            tprint(f"  ⚠️  [{self.ROLE}] SkillDesigner aborted "
                   f"({result.get('reason', 'no content') if result else 'empty result'})")
            return None

        # Write into skills/<agent>/auto/<slug>.md
        target = _RULES_DIR.parent / "skills" / self.SKILL_KEY / "auto" / f"{slug}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result["content"], encoding="utf-8")
        tprint(f"  ✨ [{self.ROLE}] new skill saved → "
               f"skills/{self.SKILL_KEY}/auto/{slug}.md  "
               f"(confidence: {result.get('confidence', '?')})")

        # Reload + return the freshly written skill so caller can record it.
        for s in list_skills(self.SKILL_KEY):
            if s["skill_key"] == slug:
                return s
        return None

    def _record_mode_from_output(self, output: str,
                                   task_text: str | None = None) -> None:
        """Inspect LLM reply for `MODE: <name>` and update active-skill
        bookkeeping. If the LLM forgot to emit the tag, fall back to the
        legacy keyword scorer on the task text so analytics keep getting
        rows (mark `method='fallback_keyword'` so the source is auditable)."""
        if not self.SKILL_KEY:
            return
        try:
            from pipeline.skill_selector import list_skills, select_skills
            skills = list_skills(self.SKILL_KEY)
        except (ImportError, AttributeError, OSError):
            return

        method = "llm_self"
        name = _parse_mode_tag(output)
        match = None
        if name and skills:
            match = next((s for s in skills if s["skill_key"] == name), None)
            if match is None:
                # LLM hallucinated a skill name not in this agent's folder
                method = "fallback_keyword"
                name = None

        if match is None:
            # Fallback: run the legacy keyword scorer on the task (or output
            # text as a last resort) so we still get a row in skill_outcomes.
            try:
                hint = task_text or output[:600]
                picked = select_skills(self.SKILL_KEY, task=hint,
                                        project_context=self.project_context,
                                        llm_auto=False, max_n=1)
                if picked:
                    match = picked[0]
                    name = match["skill_key"]
                    method = "fallback_keyword"
            except (ImportError, AttributeError, KeyError, ValueError):
                pass
            if match is None:
                # Last resort: ask SkillDesigner to write a fresh skill on
                # the fly so the gap is filled for future calls.
                created = self._auto_create_skill(task_text or output[:600])
                if created is not None:
                    match = created
                    name = created["skill_key"]
                    method = "auto_created"
                else:
                    return

        match.setdefault("detected_scope", (match.get("scope") or [None])[0])
        match["selection_method"] = method
        match["rank"] = 1
        self._active_skills = [match]
        self._skill_usage_log.append({
            "step":   self._current_step,
            "skill":  name,
            "scope":  match.get("detected_scope"),
            "method": method,
            "rank":   1,
        })
        suffix = "(self-picked by LLM)" if method == "llm_self" \
                 else "(LLM didn't emit MODE — keyword fallback)"
        tprint(f"  🎯 [{self.ROLE}] mode: {name}  {suffix}")

    # ── Anti-hallucination anchor — applies to every agent every call ──────
    _GROUND_RULE = (
        "## GROUND RULE (mandatory, non-negotiable)\n\n"
        "Mọi điều bạn output PHẢI dựa trên thực tế kiểm chứng được:\n\n"
        "1. **Đọc trước khi nói.** Trước khi khẳng định 1 file / class / "
        "API / library / version tồn tại → DÙNG `Read` / `Glob` / `Grep` / "
        "`Bash` để verify. Đặc biệt với version số, signature, import path.\n"
        "2. **Không bịa code mẫu.** Code emit phải compile-able trong project "
        "thực — nếu không chắc, `Read` file đang có để mirror style/imports "
        "hoặc `Bash` chạy lint/typecheck.\n"
        "3. **Không bịa fact.** Số liệu (benchmark, performance, statistic) "
        "phải có nguồn — citation file:line hoặc external doc URL.\n"
        "4. **Khi không chắc — KHAI BÁO.** Output dòng "
        "`MISSING_INFO: <thông tin cần> — MUST_ASK: <user|TechLead|BA>` "
        "thay vì đoán. Đoán ẩn = bug khi đến tay user.\n"
        "5. **Citation bắt buộc** khi reference codebase: ghi `path/to/file.ext:LINE` "
        "(ví dụ `lib/auth/oauth_service.dart:42`).\n"
        "6. **Khi LLM tool báo lỗi** (Read fail, Bash exit ≠ 0) → chấp nhận, "
        "report lỗi cho user, KHÔNG fabricate kết quả \"như thể đã chạy\".\n"
        "7. **Khi KHÔNG HIỂU yêu cầu user** (yêu cầu mơ hồ / thiếu input / "
        "có nhiều cách hiểu / cần stakeholder quyết định) → output dòng "
        "`ASK_USER: <câu hỏi cụ thể, ngắn gọn>` ngay từ đầu reply, **dừng** "
        "không chạy task chính. Pipeline sẽ tự pause + show câu hỏi cho user, "
        "đợi answer rồi re-run agent. Tốt hơn là làm sai 100% rồi phải fix.\n"
    )

    def _build_system(self, system: str) -> str:
        """Prepend project context + ground-rule anchor to system prompt.

        Layer order in final prompt:
          1. Agent rule + skills menu (from caller)
          2. Project cwd hint OR project_context
          3. GROUND RULE — last so it's the closest anchor before user msg

        When `self.cwd` is set (maintain mode), the claude CLI runs inside
        the project and has native Read/Glob/Grep — so we don't paste the
        pre-scanned project_context string. Just a one-line pointer, which
        keeps the prompt small and forces the LLM to read source on demand.
        """
        parts = [system]
        if self.cwd:
            parts.append(
                f"---\n\nYou are running inside the user's project at "
                f"`{self.cwd}`. Use your native Read/Glob/Grep/Edit/Write/Bash "
                f"tools to inspect and modify files directly."
            )
        elif self.project_context:
            parts.append(f"---\n\n## EXISTING PROJECT CONTEXT\n{self.project_context}")
        # Ground rule comes LAST so it's the most recent context the model
        # sees before the user message — strongest anchor against hallucination.
        parts.append(f"---\n\n{self._GROUND_RULE}")
        return "\n\n".join(parts)

    _MAX_RETRIES = 3
    _RETRY_BASE_WAIT = 2  # seconds, doubles each attempt
    # Maximum ASK_USER → answer → re-run rounds per single _call. After
    # this many rounds we give up to avoid infinite loops with a confused
    # model. 2 rounds is usually enough; if not, the agent should commit
    # to a best-effort answer with MISSING_INFO.
    _MAX_ASK_USER_ROUNDS = 2

    def _call(self, system: str, user_message: str) -> str:
        """Call Claude Code CLI. Auth is handled by `claude` itself.

        One mechanical step: we strip ANTHROPIC_API_KEY from the subprocess
        env ONLY (user's shell env is untouched) so a stale/invalid key
        never wins over the logged-in Claude Code session.

        After every successful subprocess call we look for `ASK_USER: ...`
        lines in the reply; if any, we pause, prompt the user, then re-run
        the call with the user's answer appended to the original message
        (max `_MAX_ASK_USER_ROUNDS` rounds).
        """
        system_text = self._build_system(system)
        rounds = 0
        current_user_message = user_message
        while True:
            output = self._do_call(system_text, current_user_message)
            from pipeline.parsers import extract_ask_user
            questions = extract_ask_user(output)
            if not questions or rounds >= self._MAX_ASK_USER_ROUNDS:
                if questions:
                    tprint(f"  ⚠️  [{self.ROLE}] reached ASK_USER round limit "
                           f"({self._MAX_ASK_USER_ROUNDS}) — proceeding with last reply.")
                return output
            answers = self._collect_answers_from_user(questions)
            if not answers.strip():
                # User declined to clarify — accept reply as-is
                return output
            current_user_message = (
                f"{user_message}\n\n"
                f"=== USER CLARIFICATION (round {rounds + 1}) ===\n{answers}\n"
            )
            rounds += 1

    def _collect_answers_from_user(self, questions: list[str]) -> str:
        """Show each ASK_USER question to the human, collect answers.
        Returns combined text ready to append to the user prompt.
        Empty string if user skips (Enter on first prompt → accept reply)."""
        tprint(f"\n  {'─' * 60}")
        tprint(f"  ❓ [{self.ROLE}] cần làm rõ trước khi tiếp tục")
        tprint(f"  {'─' * 60}")
        out: list[str] = []
        for i, q in enumerate(questions, 1):
            tprint(f"  Q{i}: {q}")
            try:
                ans = input(f"  A{i}> ").strip()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if not ans:
                # Empty answer on first question → user wants agent to proceed
                # with best-effort. Skip the rest.
                if i == 1:
                    tprint(f"  ↪ no answer — agent sẽ tự đoán best-effort.")
                    return ""
                ans = "(no answer)"
            out.append(f"Q{i}: {q}\nA{i}: {ans}")
        return "\n\n".join(out)

    def _do_call(self, system_text: str, user_message: str) -> str:
        """Single subprocess round-trip — separated from `_call` so the
        ASK_USER loop above can iterate cleanly without re-acquiring the
        global semaphore for the same logical request."""
        # Rate-limit: acquire semaphore + enforce min spacing between call starts
        _CALL_SEMAPHORE.acquire()
        try:
            with _SPACING_LOCK:
                now_ms = time.monotonic() * 1000
                wait_ms = _CALL_MIN_SPACING_MS - (now_ms - _LAST_CALL_TIME[0])
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000)
                _LAST_CALL_TIME[0] = time.monotonic() * 1000

            return self._call_with_retry(system_text, user_message)
        finally:
            _CALL_SEMAPHORE.release()

    def _call_with_retry(self, system_text: str, user_message: str) -> str:
        last_err: Exception | None = None
        combined_input = f"<system>\n{system_text}\n</system>\n\n{user_message}"
        # Sanitize surrogate escapes (e.g. \udcXX) that leak in from
        # non-UTF-8 filesystem paths or project files scanned in maintain
        # mode. subprocess(text=True) encodes stdin strictly; unpaired
        # surrogates would raise UnicodeEncodeError. Replace → U+FFFD.
        combined_input = combined_input.encode("utf-8", errors="replace").decode("utf-8")
        for attempt in range(1, self._MAX_RETRIES + 1):
            tmp = None
            try:
                result = subprocess.run(
                    ["claude"],
                    input=combined_input,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    cwd=self.cwd,     # project root when maintain mode — LLM's
                                       # native Read/Edit/Write tools anchor here
                )
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    stdout = (result.stdout or "").strip()
                    combined = stderr or stdout or "(no output on stderr or stdout)"
                    lower = combined.lower()
                    if any(k in lower for k in ("unknown option", "unrecognized",
                                                 "invalid argument", "unknown flag")):
                        raise RuntimeError(
                            f"CLI flag error (not retryable): {combined[:400]}\n"
                            "The `claude` CLI seems to have changed its flags. "
                            "Run `mag --doctor` to verify."
                        )
                    if any(k in lower for k in ("rate limit", "too many requests",
                                                 "quota", "429")):
                        raise RuntimeError(f"Rate-limited (retryable): {combined[:400]}")
                    raise RuntimeError(
                        f"CLI error (exit {result.returncode}): {combined[:400]}"
                    )
                output = result.stdout.strip()
                if not output:
                    raise RuntimeError(
                        "Empty output from CLI — claude returned 0 but no text. "
                        "May be a CLI version mismatch; run `mag --doctor`."
                    )
                if self.token_tracker is not None:
                    self.token_tracker.record(
                        self.ROLE,
                        self._current_step or "call",
                        system_text + user_message,
                        output,
                    )
                # Best-effort: capture the `MODE: <skill>` tag the LLM
                # picked from the WORKING MODES menu, so analytics know
                # which skill was actually applied.
                self._record_mode_from_output(output)
                return output

            except Exception as e:
                last_err = e
                if "not retryable" in str(e) or attempt == self._MAX_RETRIES:
                    break
                wait = self._RETRY_BASE_WAIT ** attempt
                tprint(f"\n  ⚠️  [{self.ROLE}] Call failed (attempt {attempt}/{self._MAX_RETRIES}): {type(e).__name__}: {e}")
                tprint(f"     Retrying in {wait}s...")
                time.sleep(wait)
        raise AgentCallError(
            role=self.ROLE,
            attempts=self._MAX_RETRIES,
            last_error=last_err if last_err else RuntimeError("unknown"),
        )

    def _call_with_image(self, system: str, user_message: str, image_path: str) -> str:
        img = Path(image_path)
        if not img.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        system_text = self._build_system(system)
        combined_input = f"<system>\n{system_text}\n</system>\n\n{user_message}"
        combined_input = combined_input.encode("utf-8", errors="replace").decode("utf-8")
        result = subprocess.run(
            ["claude", "--image", str(img)],
            input=combined_input,
            capture_output=True, text=True, timeout=300,
            cwd=self.cwd,
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or (result.stdout or "").strip()
            raise RuntimeError(f"Vision CLI error (exit {result.returncode}): {err[:400]}")
        return (result.stdout or "").strip()

    def ask(self, target: BaseAgent, question: str) -> str:
        """Send a question to another agent, get response, log in bus."""
        sep = "─" * 60
        tprint(f"\n  {sep}")
        tprint(f"  💬 {self.ROLE} ──► {target.ROLE}")
        tprint(f"  {sep}")
        tprint(f"  ❓ Question:")
        for line in question.strip().splitlines():
            tprint(f"     {line}")

        msg = self.message_bus.send(self.ROLE, target.ROLE, question)
        response = target.respond_to(self.ROLE, question)
        self.message_bus.reply(msg, response)

        tprint(f"\n  💡 {target.ROLE} answers:")
        for line in response.strip().splitlines():
            tprint(f"     {line}")
        tprint(f"  {sep}")
        return response

    # Keep each recent-history excerpt short so 3 turns don't blow the prompt.
    _RESPOND_HISTORY_TURNS  = 3
    _RESPOND_HISTORY_CHARS  = 400   # per question/answer chunk

    def respond_to(self, from_role: str, question: str) -> str:
        """Answer a question from another agent. Concise, expert reply.

        Now context-aware: includes the last few Q&A turns involving this
        agent so repeated clarifications don't start from zero each time.
        Reduces clarification cascades when Dev asks BA multiple rounds.
        """
        history_block = ""
        if self.message_bus is not None:
            recent = self.message_bus.recent(
                self.ROLE, n=self._RESPOND_HISTORY_TURNS,
            )
            if recent:
                lines = ["", "## Recent conversation involving you (most recent last)"]
                max_chars = self._RESPOND_HISTORY_CHARS
                for msg in recent:
                    q = (msg.content or "").strip()
                    a = (msg.response or "").strip()
                    if len(q) > max_chars:
                        q = q[:max_chars] + "…"
                    if len(a) > max_chars:
                        a = a[:max_chars] + "…"
                    lines.append(f"\n— {msg.from_agent} → {msg.to_agent}:")
                    lines.append(f"  Q: {q}")
                    if a:
                        lines.append(f"  A: {a}")
                history_block = "\n".join(lines)

        system = self._build_system(
            f"{self.system_prompt}\n\n"
            f"Colleague {from_role} is asking you a domain question on the project. "
            "Answer concisely, to the point, max 150 words. "
            "If you've already addressed part of this in the recent conversation "
            "below, reference that instead of repeating yourself."
            f"{history_block}"
        )
        return self._call(system, f"{from_role} asks: {question}")

    def plan_needed_info(self, task_description: str, available_context: str) -> list[dict]:
        """
        Pre-produce check: agent lists what info it needs before producing output.
        Returns list of {need: str, source: "BA"|"TechLead"|"PM"|"User"}
        """
        prompt = f"""Before starting, review available info and list what is missing.

=== TASK ===
{task_description}

=== AVAILABLE INFO ===
{available_context}

For each piece of info TRULY needed to complete the task (don't ask about things you can decide yourself):
NEED: [specific info required] — SOURCE: [BA | TechLead | PM | User]

If info is sufficient:
READY: YES"""

        raw = self._call(
            f"You are {self.ROLE}. Evaluate available info before starting. Be concise.",
            prompt,
        )

        if "READY: YES" in raw:
            return []

        needs = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("NEED:"):
                parts = line[5:].split("— SOURCE:", 1)
                if len(parts) == 2:
                    needs.append({
                        "need": parts[0].strip(),
                        "source": parts[1].strip().upper(),
                    })
                elif parts:
                    needs.append({"need": parts[0].strip(), "source": "User"})
        return needs

    def revise(self, original_output: str, revision_guide: list[str], original_prompt: str) -> str:
        """Revise own output based on critic's feedback."""
        guide_text = "\n".join(f"- {g}" for g in revision_guide)
        system = (
            f"{self.system_prompt}\n\n"
            "You just received Critic feedback and must improve your output. "
            "Keep what is good, fix the pointed-out issues."
        )
        prompt = f"""Your original output:
{original_output}

Critic revisions required:
{guide_text}

Original context:
{original_prompt}

Rebuild the improved output following the guidance above."""
        return self._call(system, prompt)

    def reset(self):
        pass
