"""Base agent using Claude Code CLI — no separate API key needed."""
from __future__ import annotations
import subprocess
import base64
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.message_bus import MessageBus
    from core.token_tracker import TokenTracker

_RULES_DIR = Path(__file__).parent.parent / "rules"

# Global semaphore: cap concurrent Claude CLI calls to avoid quota spike.
# Default 3 — adjustable via env var.
_MAX_CONCURRENT = int(os.environ.get("MULTI_AGENT_MAX_CONCURRENT", "3"))
_CALL_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT)

# Minimum spacing between call starts (ms) — avoids burst rate limit.
_CALL_MIN_SPACING_MS = int(os.environ.get("MULTI_AGENT_CALL_SPACING_MS", "100"))
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
        self._current_step: str = ""   # set by orchestrator before each step
        self._active_skill: dict | None = None  # set by detect_skill()
        self._skill_usage_log: list[dict] = []  # for skill optimizer

    @property
    def system_prompt(self) -> str:
        """Load from rules/<profile>/<RULE_KEY>.md, fallback to default. Reloads every call.
        If an active skill is set, merge skill content into system prompt."""
        base = _load_rule(self.RULE_KEY, self.profile) if self.RULE_KEY else self._system_prompt
        if self._active_skill:
            try:
                from learning.skill_selector import render_skill
                return render_skill(self._active_skill, base)
            except Exception:
                return base
        return base

    def detect_skill(self, task: str, scope_hint: str | None = None) -> dict | None:
        """Auto-select best skill for this task. Falls back to LLM when heuristics ambiguous."""
        if not self.SKILL_KEY:
            return None
        try:
            from learning.skill_selector import select_skill, llm_pick_skill
        except ImportError:
            return None

        def _llm(key, t, candidates):
            return llm_pick_skill(self._call, key, t, candidates)

        skill = select_skill(
            self.SKILL_KEY,
            task=task,
            project_context=self.project_context,
            scope_hint=scope_hint,
            llm_fallback=_llm,
        )
        if skill:
            self._active_skill = skill
            self._skill_usage_log.append({
                "step":    self._current_step,
                "skill":   skill["skill_key"],
                "scope":   skill.get("detected_scope"),
                "method":  skill.get("selection_method"),
            })
            print(f"  🎯 [{self.ROLE}] skill: {skill['skill_key']}  scope={skill.get('detected_scope')}  via={skill.get('selection_method')}")
        return skill

    def clear_skill(self):
        """Reset active skill (e.g. between independent tasks)."""
        self._active_skill = None

    def _build_system(self, system: str) -> str:
        """Prepend project context to system prompt when available."""
        if self.project_context:
            return f"{system}\n\n---\n\n## EXISTING PROJECT CONTEXT\n{self.project_context}"
        return system

    _MAX_RETRIES = 3
    _RETRY_BASE_WAIT = 2  # seconds, doubles each attempt

    def _call(self, system: str, user_message: str, max_tokens: int = 4096) -> str:
        import tempfile
        system_text = self._build_system(system)
        env = {**os.environ}
        env.pop("ANTHROPIC_API_KEY", None)

        # Rate-limit: acquire semaphore + enforce min spacing between call starts
        _CALL_SEMAPHORE.acquire()
        try:
            with _SPACING_LOCK:
                now_ms = time.monotonic() * 1000
                wait_ms = _CALL_MIN_SPACING_MS - (now_ms - _LAST_CALL_TIME[0])
                if wait_ms > 0:
                    time.sleep(wait_ms / 1000)
                _LAST_CALL_TIME[0] = time.monotonic() * 1000

            return self._call_with_retry(system_text, user_message, env)
        finally:
            _CALL_SEMAPHORE.release()

    def _call_with_retry(self, system_text: str, user_message: str, env: dict) -> str:
        import tempfile
        last_err: Exception | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            tmp = None
            try:
                # Write system prompt to temp file to avoid shell arg size limits
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                                  delete=False, encoding="utf-8")
                tmp.write(system_text)
                tmp.flush()
                tmp.close()

                result = subprocess.run(
                    [
                        "claude", "-p", user_message,
                        "--system-prompt-file", tmp.name,
                        "--output-format", "text",
                        "--bare",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,
                )
                if result.returncode != 0:
                    err_msg = result.stderr.strip()
                    if any(k in err_msg.lower() for k in ("authentication", "api key", "unauthorized", "403")):
                        raise RuntimeError(f"Auth error (not retryable): {err_msg}")
                    raise RuntimeError(f"CLI error (exit {result.returncode}): {err_msg}")
                output = result.stdout.strip()
                if not output:
                    raise RuntimeError("Empty output from CLI")
                if self.token_tracker is not None:
                    self.token_tracker.record(
                        self.ROLE,
                        self._current_step or "call",
                        system_text + user_message,
                        output,
                    )
                return output

            except (subprocess.TimeoutExpired, RuntimeError) as e:
                last_err = e
                if "not retryable" in str(e) or attempt == self._MAX_RETRIES:
                    break
                wait = self._RETRY_BASE_WAIT ** attempt
                print(f"\n  ⚠️  [{self.ROLE}] Call failed (attempt {attempt}/{self._MAX_RETRIES}): {e}")
                print(f"     Retrying in {wait}s...")
                time.sleep(wait)
            finally:
                if tmp:
                    Path(tmp.name).unlink(missing_ok=True)

        raise RuntimeError(f"[{self.ROLE}] CLI failed after {self._MAX_RETRIES} attempts: {last_err}")

    def _call_with_image(self, system: str, user_message: str, image_path: str, max_tokens: int = 2048) -> str:
        """Vision call via Anthropic SDK — used for reviewing screenshots."""
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

        img = Path(image_path)
        if not img.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        media_type = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
        image_data = base64.standard_b64encode(img.read_bytes()).decode("utf-8")

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self._build_system(system),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": user_message},
                ],
            }],
        )
        return response.content[0].text.strip()

    def ask(self, target: BaseAgent, question: str) -> str:
        """Send a question to another agent, get response, log in bus."""
        sep = "─" * 60
        print(f"\n  {sep}")
        print(f"  💬 {self.ROLE} ──► {target.ROLE}")
        print(f"  {sep}")
        print(f"  ❓ Question:")
        for line in question.strip().splitlines():
            print(f"     {line}")

        msg = self.message_bus.send(self.ROLE, target.ROLE, question)
        response = target.respond_to(self.ROLE, question)
        self.message_bus.reply(msg, response)

        print(f"\n  💡 {target.ROLE} answers:")
        for line in response.strip().splitlines():
            print(f"     {line}")
        print(f"  {sep}")
        return response

    def respond_to(self, from_role: str, question: str) -> str:
        """Answer a question from another agent. Concise, expert reply."""
        system = self._build_system(
            f"{self.system_prompt}\n\n"
            f"Colleague {from_role} is asking you a domain question on the project. "
            "Answer concisely, to the point, max 150 words."
        )
        return self._call(system, f"{from_role} hỏi: {question}", max_tokens=600)

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
            max_tokens=400,
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
        return self._call(system, prompt, max_tokens=6000)

    def reset(self):
        pass
