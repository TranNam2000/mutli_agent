"""
Automate Stitch web UI (https://stitch.withgoogle.com).

Hardened for production use:
  * Retry with exponential backoff on transient failures
  * CAPTCHA / rate-limit detection with human handoff
  * Concurrent-session file lock (avoids two pipelines racing on 1 browser)
  * Multi-strategy prompt-input detection (selectors → role → vision)
  * Stability-based generation wait that ignores cursor-blink noise
  * Session validity ping before the main call — auto re-login when expired
  * Transient vs permanent error classification

First-time setup:
    python stitch_browser.py --login

Runtime (from orchestrator):
    from testing.stitch_browser import generate_and_screenshot
    path = generate_and_screenshot(prompt, session_id, round_num=1)
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


STITCH_URL       = "https://stitch.withgoogle.com/"
SCREENSHOT_DIR   = Path("outputs/stitch_screenshots")
SESSION_FILE     = Path("outputs/stitch_session.json")
LOCK_FILE        = Path("outputs/.stitch.lock")

# Retry / rate-limit config (env-overridable).
MAX_RETRIES      = int(os.environ.get("STITCH_MAX_RETRIES",       "3"))
RETRY_BASE_WAIT  = int(os.environ.get("STITCH_RETRY_BASE_WAIT",   "4"))
RATE_LIMIT_SEC   = int(os.environ.get("STITCH_RATE_LIMIT_SEC",    "5"))
GEN_TIMEOUT_MS   = int(os.environ.get("STITCH_GEN_TIMEOUT_MS",    "120000"))
NAV_TIMEOUT_MS   = int(os.environ.get("STITCH_NAV_TIMEOUT_MS",    "30000"))
HEADLESS_DEFAULT = os.environ.get("STITCH_HEADLESS", "1") == "1"


# ── Error taxonomy ────────────────────────────────────────────────────────────

class StitchError(Exception):
    """Base class for stitch automation failures."""


class TransientStitchError(StitchError):
    """Retryable — network timeout, DOM race, mid-navigation glitches."""


class PermanentStitchError(StitchError):
    """Non-retryable — bad input, account suspended, invalid URL."""


class CaptchaStitchError(StitchError):
    """CAPTCHA / rate-limit block — requires human intervention."""


# ── Concurrency lock ─────────────────────────────────────────────────────────

class _ConcurrentLock:
    """File-based advisory lock so two pipelines don't race on one profile.

    Exits with PermanentStitchError if another session holds the lock for
    longer than `wait_sec` seconds.
    """

    def __init__(self, path: Path, wait_sec: int = 180):
        self._path = Path(path)
        self._wait = wait_sec
        self._fd = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self._path, "w")
        deadline = time.time() + self._wait
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd.write(str(os.getpid()))
                self._fd.flush()
                return self
            except BlockingIOError:
                if time.time() > deadline:
                    self._fd.close()
                    raise PermanentStitchError(
                        f"Another stitch session is running (lock at {self._path})."
                    )
                time.sleep(1)

    def __exit__(self, exc_type, exc, tb):
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fd.close()
        except Exception:
            pass


# ── Session helpers ──────────────────────────────────────────────────────────

def save_session(context) -> None:
    """Save browser cookies + localStorage to disk."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = context.storage_state()
    SESSION_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"  💾 Session saved → {SESSION_FILE}")


def load_session(p, headless: bool = HEADLESS_DEFAULT) -> tuple:
    """
    Launch browser with saved session if exists, fresh otherwise.
    Returns (browser, context, is_fresh_login).
    """
    browser = p.chromium.launch(headless=headless)
    if SESSION_FILE.exists():
        try:
            state = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            context = browser.new_context(
                storage_state=state,
                viewport={"width": 1440, "height": 900},
            )
            print("  ✅ Reusing saved session — no login needed.")
            return browser, context, False
        except Exception as e:
            print(f"  ⚠️  Session corrupted ({e}) — will need fresh login.")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    return browser, context, True


def _session_age_days() -> float:
    """Return age of saved session file in days. 999 if missing."""
    if not SESSION_FILE.exists():
        return 999.0
    age_sec = time.time() - SESSION_FILE.stat().st_mtime
    return age_sec / 86400.0


# ── One-time login ───────────────────────────────────────────────────────────

def login() -> None:
    """Open browser, let user log in manually, save session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    print("\n  🔑 LOGIN MODE — sign in to Google on the browser, then come back here and press Enter.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page    = context.new_page()
        try:
            page.goto(STITCH_URL, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            print(f"  ⚠️  Initial navigation timed out ({e}) — continue anyway.")

        print("  Browser opened — sign in to Google on Stitch.")
        input("  Press Enter once signed in and Stitch has loaded... ")

        save_session(context)
        browser.close()
    print("  ✅ Login done. Future runs will reuse this session automatically.")


# ── CAPTCHA / rate-limit detection ───────────────────────────────────────────

def _is_captcha_present(page) -> bool:
    """Detect the three most common blockers: reCAPTCHA, hCaptcha, quota notice."""
    markers = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[title*='recaptcha' i]",
        "text=/i'?m not a robot/i",
        "text=/verify you are human/i",
        "text=/rate limit/i",
        "text=/too many requests/i",
        "text=/temporarily unavailable/i",
    ]
    for m in markers:
        try:
            if page.locator(m).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def _needs_login(page) -> bool:
    """Heuristic: is page showing the Google sign-in UI?"""
    markers = [
        "text=/sign in to google/i",
        "text=/sign in/i",
        "input[type='email']",
        "[aria-label*='sign in' i]",
    ]
    for m in markers:
        try:
            if page.locator(m).first.is_visible(timeout=1500):
                return True
        except Exception:
            continue
    return False


# ── Rate limiting ────────────────────────────────────────────────────────────

_LAST_CALL_FILE = Path("outputs/.stitch_last_call")

def _respect_rate_limit() -> None:
    """Sleep enough that at least RATE_LIMIT_SEC has passed since last call."""
    try:
        _LAST_CALL_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _LAST_CALL_FILE.exists():
            last = float(_LAST_CALL_FILE.read_text().strip() or "0")
            delta = time.time() - last
            if delta < RATE_LIMIT_SEC:
                wait = RATE_LIMIT_SEC - delta
                print(f"  ⏱  rate-limit sleep {wait:.1f}s")
                time.sleep(wait)
        _LAST_CALL_FILE.write_text(str(time.time()))
    except Exception as e:
        print(f"  ⚠️  rate-limit bookkeeping failed ({e}) — continuing.")


# ── Main generate function ───────────────────────────────────────────────────

def generate_and_screenshot(prompt: str, session_id: str, round_num: int = 1,
                              headless: bool | None = None) -> str:
    """Submit a prompt to Stitch and return a screenshot path.

    Retries up to MAX_RETRIES on transient errors with exponential backoff.
    Raises CaptchaStitchError if Google blocks the automation — caller should
    prompt the user to complete the CAPTCHA manually.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    effective_headless = HEADLESS_DEFAULT if headless is None else headless

    # Session older than 7 days → warn; Google cookies commonly expire.
    age = _session_age_days()
    if age > 7 and age < 999:
        print(f"  ⚠️  Session file is {age:.1f} days old — may require re-login.")

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _attempt_generate(prompt, session_id, round_num,
                                       effective_headless, attempt)
        except CaptchaStitchError:
            # CAPTCHA can't be auto-solved — escalate immediately.
            raise
        except TransientStitchError as e:
            last_exc = e
            wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
            print(f"  ⚠️  attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                print(f"     retrying in {wait}s...")
                time.sleep(wait)
        except PermanentStitchError:
            raise
        except Exception as e:
            # Unknown error — treat as transient for the first retry.
            last_exc = e
            wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
            print(f"  ⚠️  attempt {attempt}/{MAX_RETRIES} unexpected error: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(wait)

    raise TransientStitchError(
        f"All {MAX_RETRIES} retries exhausted. Last error: {last_exc}"
    )


def _attempt_generate(prompt: str, session_id: str, round_num: int,
                        headless: bool, attempt: int) -> str:
    """One attempt at generate + screenshot (wrapped in lock + rate limit)."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    except ImportError:
        raise PermanentStitchError(
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )

    ts = datetime.now().strftime("%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"{session_id}_round{round_num}_{ts}.png"

    with _ConcurrentLock(LOCK_FILE):
        _respect_rate_limit()

        with sync_playwright() as p:
            browser, context, is_fresh = load_session(p, headless=headless)
            page = context.new_page()

            # Navigate with graceful timeout handling.
            print(f"  🌐 Opening Stitch (attempt {attempt})...")
            try:
                page.goto(STITCH_URL, wait_until="networkidle",
                            timeout=NAV_TIMEOUT_MS)
            except PWTimeoutError:
                # networkidle can fail on busy SPA; try a softer load state.
                try:
                    page.goto(STITCH_URL, wait_until="load",
                                timeout=NAV_TIMEOUT_MS)
                except Exception as e:
                    browser.close()
                    raise TransientStitchError(f"Navigation failed: {e}")

            # CAPTCHA check — always before touching input.
            if _is_captcha_present(page):
                if headless:
                    browser.close()
                    raise CaptchaStitchError(
                        "CAPTCHA detected in headless mode — re-run with "
                        "STITCH_HEADLESS=0 to complete it manually."
                    )
                print("  🚨 CAPTCHA detected. Please solve it in the browser.")
                input("  Press Enter once CAPTCHA is cleared... ")
                if _is_captcha_present(page):
                    browser.close()
                    raise CaptchaStitchError("CAPTCHA still present after human attempt.")

            # Re-login flow if session expired.
            if is_fresh or _needs_login(page):
                if headless:
                    browser.close()
                    raise PermanentStitchError(
                        "Session expired and headless mode is active. "
                        "Re-run `python stitch_browser.py --login` first."
                    )
                print("  ⚠️  Session expired — log in manually, then press Enter.")
                input("  Press Enter once logged in... ")
                save_session(context)

            # Find and fill the prompt input.
            prompt_input = _find_prompt_input(page)
            if prompt_input is None:
                # Fall back to manual paste so user isn't blocked.
                print("  ⚠️  Could not locate the input automatically.")
                print("  📋 Paste this prompt into Stitch manually:")
                print(f"\n{'─' * 60}")
                print(prompt[:1500])
                print(f"{'─' * 60}")
                input("  Press Enter once Stitch has finished generating... ")
            else:
                print("  ✍️  Typing prompt into Stitch...")
                try:
                    prompt_input.click()
                    prompt_input.fill(prompt[:2000])
                    prompt_input.press("Enter")
                except Exception as e:
                    browser.close()
                    raise TransientStitchError(f"Could not type prompt: {e}")
                print("  ⏳ Waiting for Stitch to generate...")
                _wait_for_generation(page)

            # Refresh session cookie expiry.
            save_session(context)

            try:
                page.screenshot(path=str(screenshot_path), full_page=False)
            except Exception as e:
                browser.close()
                raise TransientStitchError(f"Screenshot failed: {e}")

            print(f"  📸 Screenshot saved → {screenshot_path}")
            browser.close()

    return str(screenshot_path)


# ── Prompt-input locators ────────────────────────────────────────────────────

_PROMPT_SELECTORS = [
    "textarea[placeholder*='describe' i]",
    "textarea[placeholder*='prompt' i]",
    "textarea[placeholder*='design' i]",
    "textarea[placeholder*='type' i]",
    "[data-testid*='prompt' i]",
    "[aria-label*='prompt' i]",
    "[aria-label*='describe' i]",
    "textarea",
    "[contenteditable='true']",
]


def _find_prompt_input(page):
    """Three-tier locator chain: selectors → role → Claude vision."""
    # Tier 1: named selectors.
    for sel in _PROMPT_SELECTORS:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                print(f"  ✅ Input found via selector: {sel}")
                return el
        except Exception:
            continue

    # Tier 2: accessibility role.
    try:
        el = page.get_by_role("textbox").first
        if el.is_visible(timeout=2000):
            print("  ✅ Input found via role=textbox")
            return el
    except Exception:
        pass

    # Tier 3: Claude vision on screenshot.
    print("  🔍 Selectors failed — using Claude vision to detect input...")
    return _vision_detect_input(page)


def _vision_detect_input(page):
    """Screenshot + ask Claude for input coords; click at returned X/Y."""
    tmp_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        page.screenshot(path=tmp_img.name, full_page=False)
    except Exception as e:
        print(f"  ⚠️  Vision screenshot failed: {e}")
        return None

    prompt = (
        "Here is a screenshot of Stitch (stitch.withgoogle.com). "
        "Find the text input field where the user types their UI prompt. "
        "Reply with the centre coordinates in this exact format: X=<int> Y=<int>. "
        "If no input field is visible, reply: NOT_FOUND"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--image", tmp_img.name,
             "--output-format", "text", "--bare"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ},
        )
        raw = result.stdout.strip()
        mx = re.search(r"X=(\d+)", raw)
        my = re.search(r"Y=(\d+)", raw)
        if mx and my:
            x, y = int(mx.group(1)), int(my.group(1))
            print(f"  🎯 Claude vision detected input at ({x}, {y})")
            page.mouse.click(x, y)
            page.wait_for_timeout(500)
            return _CoordInput(page, x, y)
    except subprocess.TimeoutExpired:
        print("  ⚠️  Claude vision call timed out — giving up.")
    except Exception as e:
        print(f"  ⚠️  Vision detect failed: {e}")
    finally:
        try:
            os.unlink(tmp_img.name)
        except Exception:
            pass
    return None


class _CoordInput:
    """Pseudo-element for coordinate-based text input."""
    def __init__(self, page, x: int, y: int):
        self._page = page
        self._x = x
        self._y = y

    def click(self):
        self._page.mouse.click(self._x, self._y)

    def fill(self, text: str):
        self._page.mouse.click(self._x, self._y)
        self._page.keyboard.press("Control+A")
        self._page.keyboard.type(text, delay=10)

    def press(self, key: str):
        self._page.keyboard.press(key)

    def is_visible(self, timeout: int = 0) -> bool:  # duck-type compat
        return True


# ── Generation wait ──────────────────────────────────────────────────────────

def _wait_for_generation(page, timeout_ms: int | None = None) -> None:
    """Block until Stitch stops visually changing.

    Uses a clipped screenshot (the content area, excluding the status bar +
    header which may have blinking text) and requires three consecutive
    identical checks one second apart.

    Re-checks CAPTCHA during the wait so a mid-generation block doesn't hang
    forever.
    """
    timeout_ms = timeout_ms if timeout_ms is not None else GEN_TIMEOUT_MS
    print("  ⏳ Monitoring DOM stability for generation end...")

    # Wait for any initial work.
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    def _content_hash() -> str:
        try:
            vp = page.viewport_size or {"width": 1440, "height": 900}
            # Clip to the central content area (exclude top / bottom 80px).
            clip = {
                "x":      0,
                "y":      80,
                "width":  vp["width"],
                "height": max(200, vp["height"] - 160),
            }
            buf = page.screenshot(clip=clip)
            return hashlib.md5(buf).hexdigest()
        except Exception:
            # Screenshot failure (e.g. page mid-nav) — return a unique value so
            # we don't falsely detect stability.
            return f"err-{time.time()}"

    stable_count   = 0
    prev_hash: str = ""
    deadline       = time.time() + timeout_ms / 1000
    captcha_check_every = 5
    cycles         = 0

    while time.time() < deadline:
        time.sleep(1)
        cycles += 1
        if cycles % captcha_check_every == 0 and _is_captcha_present(page):
            raise CaptchaStitchError("CAPTCHA appeared mid-generation.")
        current_hash = _content_hash()
        if current_hash == prev_hash:
            stable_count += 1
            if stable_count >= 3:
                print("  ✅ Page stable — generation appears complete.")
                return
        else:
            stable_count = 0
        prev_hash = current_hash

    print("  ⚠️  Generation wait timed out — returning whatever is on screen.")


# ── CLI entrypoint ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--login" in sys.argv:
        login()
    elif "--test-prompt" in sys.argv:
        idx = sys.argv.index("--test-prompt")
        prompt = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "Simple login screen"
        try:
            path = generate_and_screenshot(prompt, "manual_test", 1, headless=False)
            print(f"\n✅ Done: {path}")
        except CaptchaStitchError as e:
            print(f"\n🚨 CAPTCHA block: {e}")
            sys.exit(2)
        except PermanentStitchError as e:
            print(f"\n❌ Fatal: {e}")
            sys.exit(1)
        except StitchError as e:
            print(f"\n❌ Error after retries: {e}")
            sys.exit(1)
    else:
        print("Usage:")
        print("  python stitch_browser.py --login")
        print("  python stitch_browser.py --test-prompt 'your prompt here'")
