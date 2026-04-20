"""
Automate Stitch web UI:
  1. Open stitch.withgoogle.com (reuse saved session if có)
  2. Paste design prompt
  3. Wait for generation
  4. Screenshot the result
  5. Return screenshot path

First-time setup:
    python stitch_browser.py --login
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from datetime import datetime


STITCH_URL    = "https://stitch.withgoogle.com/"
SCREENSHOT_DIR = Path("outputs/stitch_screenshots")
SESSION_FILE   = Path("outputs/stitch_session.json")  # saved cookies + storage


# ── Session helpers ───────────────────────────────────────────────────────────

def save_session(context):
    """Save browser cookies + localStorage to disk."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = context.storage_state()
    SESSION_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(f"  💾 Session saved → {SESSION_FILE}")


def load_session(p) -> tuple:
    """
    Launch browser with saved session if exists, fresh otherwise.
    Returns (browser, context, is_fresh_login).
    """
    browser = p.chromium.launch(headless=False)
    if SESSION_FILE.exists():
        try:
            state = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            context = browser.new_context(
                storage_state=state,
                viewport={"width": 1440, "height": 900},
            )
            print("  ✅ Use session saved — no need login again.")
            return browser, context, False
        except Exception as e:
            print(f"  ⚠️  Session bug ({e}) — login again.")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    return browser, context, True


# ── One-time login ────────────────────────────────────────────────────────────

def login():
    """
    Open browser, let user login manually to Stitch, then save session.
    Run once: python stitch_browser.py --login
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Run: pip install playwright && playwright install chromium")

    print("\n  🔑 LOGIN MODE — Đăng enter Google trên browser, then quay again here nhấn Enter.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page    = context.new_page()
        page.goto(STITCH_URL, wait_until="networkidle", timeout=30000)

        print("  Browser  mở — hãy đăng enter Google trên Stitch.")
        input("  Nhấn Enter after when  đăng enter and Stitch load done...")

        save_session(context)
        browser.close()
    print("  ✅ Login done. Time after agent tự chạy no need login.")


# ── Main generate function ────────────────────────────────────────────────────

def generate_and_screenshot(prompt: str, session_id: str, round_num: int = 1) -> str:
    """
    Open Stitch (reuse session), submit prompt, wait for generation,
    screenshot and return path.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Run: pip install playwright && playwright install chromium")

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    screenshot_path = SCREENSHOT_DIR / f"{session_id}_round{round_num}_{ts}.png"

    with sync_playwright() as p:
        browser, context, is_fresh = load_session(p)
        page = context.new_page()

        print(f"  🌐 Mở Stitch...")
        page.goto(STITCH_URL, wait_until="networkidle", timeout=30000)

        # If fresh login needed (session expired)
        if is_fresh or _needs_login(page):
            print("  ⚠️  Session hết hạn — need login again.")
            print("  Đăng enter Google trên browser, then nhấn Enter ở here.")
            input("  Nhấn Enter after when đăng enter done...")
            save_session(context)

        # Find and fill prompt input
        prompt_input = _find_prompt_input(page)
        if prompt_input is None:
            print("  ⚠️  No tìm thấy input. You paste thủ công:")
            print(f"\n{'─'*60}")
            print(prompt[:1000])
            print(f"{'─'*60}")
            input("  Nhấn Enter after when Stitch generate done...")
        else:
            print("  ✍️  Paste prompt into Stitch...")
            prompt_input.click()
            prompt_input.fill(prompt[:2000])
            prompt_input.press("Enter")
            print("  ⏳ Chờ Stitch generate UI...")
            _wait_for_generation(page)

        # Save updated session (refresh cookie expiry)
        save_session(context)

        page.screenshot(path=str(screenshot_path), full_page=False)
        print(f"  📸 Screenshot: {screenshot_path}")
        browser.close()

    return str(screenshot_path)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _needs_login(page) -> bool:
    """Check if page is showing Google login screen."""
    try:
        return page.locator("text=Sign in").is_visible(timeout=3000)
    except Exception:
        return False


def _find_prompt_input(page):
    """
    Auto-detect prompt input:
    1. Try common selectors first (fast)
    2. Inspect accessibility tree
    3. Screenshot + Claude vision as fallback
    """
    # Round 1: common selectors
    selectors = [
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
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=1500):
                print(f"  ✅ Input tìm thấy qua selector: {sel}")
                return el
        except Exception:
            continue

    # Round 2: accessibility tree — tìm role=textbox
    try:
        el = page.get_by_role("textbox").first
        if el.is_visible(timeout=2000):
            print("  ✅ Input tìm thấy qua role=textbox")
            return el
    except Exception:
        pass

    # Round 3: Claude vision — chụp ảnh  hỏi Claude tọa độ input
    print("  🔍 No tìm is selector — use Claude vision to detect...")
    return _vision_detect_input(page)


def _vision_detect_input(page):
    """
    Screenshot the page, ask Claude vision where the prompt input is,
    then click at those coordinates.
    Returns a coordinate-based pseudo-element or None.
    """
    import subprocess, tempfile, re, os

    tmp_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    page.screenshot(path=tmp_img.name, full_page=False)

    prompt = (
        "Here is screenshot of Stitch (stitch.withgoogle.com). "
        "Tìm ô enter text to user gõ prompt/mô tả UI. "
        "Trả về tọa độ trung tâm of ô đó per format: X=<number> Y=<number>. "
        "If no thấy ô nào, trả về: NOT_FOUND"
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
            print(f"  🎯 Claude vision phát current input tại ({x}, {y})")
            page.mouse.click(x, y)
            page.wait_for_timeout(500)
            # Return a handle that supports fill() via keyboard
            return _CoordInput(page, x, y)
    except Exception as e:
        print(f"  ⚠️  Vision detect thất bại: {e}")
    finally:
        os.unlink(tmp_img.name)
    return None


class _CoordInput:
    """Pseudo-element for coordinate-based text input (fallback when no tìm is selector)."""
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


def _wait_for_generation(page, timeout_ms: int = 90000):
    """
    Wait until Stitch finishes generating.
    Strategy: monitor network idle + DOM stability instead of guessing class names.
    """
    # Take baseline screenshot
    import hashlib, time

    def _page_hash():
        return hashlib.md5(page.screenshot()).hexdigest()

    print("  ⏳ Chờ generation (monitor DOM stability)...")

    # Wait for initial activity (up to 8s)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # Poll: wait until page stops changing for 3 consecutive checks (3s)
    stable_count = 0
    prev_hash = None
    deadline = time.time() + timeout_ms / 1000

    while time.time() < deadline:
        time.sleep(1)
        current_hash = _page_hash()
        if current_hash == prev_hash:
            stable_count += 1
            if stable_count >= 3:
                print("  ✅ Page ổn định — generation done.")
                return
        else:
            stable_count = 0
        prev_hash = current_hash

    print("  ⚠️  Timeout — chụp ảnh current tại.")


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--login" in sys.argv:
        login()
    else:
        print("Usage: python stitch_browser.py --login")
