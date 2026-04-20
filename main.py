#!/usr/bin/env python3
"""
Multi-Agent Product Development Pipeline (task-based flow).

Flow: BA(classify) → UI→Design→BA → TechLead(prioritize) → Dev ∥ Test

Usage:
    python main.py                                              # interactive
    python main.py "your task or idea"                          # direct
    python main.py "idea" --maintain /path/to/project           # explicit project dir
    python main.py "idea" --dev-slots 3 --sprint-hours 100      # resources
    python main.py --resume 20260418_214205                     # continue session
    python main.py --update 20260418_214205 "change desc"       # amend session
    python main.py --feedback 20260418_214205                   # feed issue back
    python main.py --list                                       # list sessions
    python main.py --profiles                                   # list profiles
    python main.py --review-ui screenshot.png --session <id>    # review UI image
    python main.py "idea" --budget 800000                       # override budget
    python main.py --reselect-plan                              # re-pick plan
"""
import sys
from pathlib import Path
from orchestrator import ProductDevelopmentOrchestrator
from core.plan_detector import detect_budget

RULES_DIR = Path(__file__).parent / "rules"

EXAMPLE_IDEAS = [
    "Mobile app that lets users download videos from YouTube, TikTok, Instagram with offline library management and multi-quality support (480p, 720p, 1080p).",
    "Social login (Google, Facebook, Apple) for existing mobile app, including profile management and session handling.",
    "Payment module: integrate VNPay, Momo, Stripe with transaction history and refunds.",
]

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║      🤖  MULTI-AGENT PRODUCT DEVELOPMENT PIPELINE  🤖           ║
║                                                                  ║
║  Flow: BA(classify) → Design → TechLead → Dev ∥ Test            ║
╚══════════════════════════════════════════════════════════════════╝
"""


def _get_arg(flag: str) -> str | None:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def _list_profiles() -> list[str]:
    return [p.name for p in RULES_DIR.iterdir() if p.is_dir() and p.name != "backups"]


def _fetch_url(url: str) -> str:
    """Fetch text content from a URL (Jira, Confluence, Google Docs, web page...)."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        # Strip HTML tags simply
        import re
        text = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{3,}", "\n\n", text).strip()
        return text[:8000]
    except urllib.error.URLError as e:
        print(f"  ⚠️  Failed to fetch URL: {e}")
        return ""


def _resolve_input(raw: str) -> str:
    """If input looks like a URL, fetch it. Otherwise return as-is."""
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        print(f"\n  🔗 Fetching content from: {raw}")
        content = _fetch_url(raw)
        if content:
            print(f"  ✅ Got {len(content)} chars from URL.")
            return f"[Source: {raw}]\n\n{content}"
        print("  ❌ Could not fetch content, using URL as input.")
        return raw
    return raw


def _prompt_user_input() -> str:
    """Interactive prompt: user enters a description or pastes a link."""
    print("\n" + "─" * 60)
    print("  📋 ENTER TASK")
    print("─" * 60)
    print("  You can:")
    print("  • Describe the feature / product to build")
    print("  • Paste a link (Jira ticket, Confluence, Google Docs, web page...)")
    print("  • Enter a number 1-3 to use a pre-loaded example")
    print("─" * 60)

    print("\n  Examples:")
    for i, idea in enumerate(EXAMPLE_IDEAS, 1):
        print(f"  {i}. {idea[:75]}...")

    print()
    user_input = input("  > ").strip()

    if user_input.isdigit() and 1 <= int(user_input) <= len(EXAMPLE_IDEAS):
        idea = EXAMPLE_IDEAS[int(user_input) - 1]
        print(f"\n  ✅ Selected: {idea[:80]}...")
        return idea

    return _resolve_input(user_input)


def _collect_feedback() -> dict:
    """Interactive prompt to collect structured product feedback."""
    TYPES = ["Bug", "UX Issue", "Missing Feature", "Performance", "Other"]
    print("\n" + "─" * 60)
    print("  💬 PRODUCT FEEDBACK")
    print("─" * 60)
    print("  Feedback type:")
    for i, t in enumerate(TYPES, 1):
        print(f"  {i}. {t}")

    fb_type = TYPES[0]
    while True:
        choice = input(f"\n  Choose [1-{len(TYPES)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(TYPES):
            fb_type = TYPES[int(choice) - 1]
            break
        print(f"  Please enter a number from 1 to {len(TYPES)}.")

    print(f"\n  Describe {fb_type} (the more detailed, the better):")
    description = input("  > ").strip()

    screenshot = ""
    if fb_type in ("Bug", "UX Issue"):
        screenshot = input("\n  Screenshot path (Enter to skip): ").strip()
        if screenshot and not Path(screenshot).exists():
            print(f"  ⚠️  File does not exist: {screenshot} — skipping.")
            screenshot = ""

    return {"type": fb_type, "description": description, "screenshot_path": screenshot}


def _run_ui_review(screenshot: str, session_id: str, profile: str = "default"):
    """Review a Stitch UI screenshot against design specs from a session."""
    from agents import DesignAgent
    from context.project_context_reader import detect_project_name

    # Find outputs dir (may be nested under project name)
    outputs = Path("outputs")
    project_name = detect_project_name(Path.cwd())
    for search_dir in [outputs / project_name, outputs]:
        design_path = search_dir / f"{session_id}_design.md"
        if design_path.exists():
            break
    else:
        print(f"❌ No design specs found for session: {session_id}")
        sys.exit(1)

    design_specs = design_path.read_text(encoding="utf-8")

    # Load BA output for user journeys context (optional)
    ba_path = design_path.parent / f"{session_id}_ba.md"
    user_journeys = ""
    if ba_path.exists():
        content = ba_path.read_text(encoding="utf-8")
        # Extract user journey section
        import re
        m = re.search(r"(?:User Journey|Journey Map)(.*?)(?=\n##|\Z)", content, re.DOTALL | re.IGNORECASE)
        user_journeys = m.group(1).strip()[:1000] if m else ""

    agent = DesignAgent(profile=profile)

    MAX_ROUNDS = 3
    print(f"\n{'═'*70}")
    print(f"  🎨 UI REVIEW — {screenshot}")
    print(f"  Session: {session_id}  Profile: {profile}")
    if user_journeys:
        print(f"  User journeys loaded from BA doc ✓")
    print(f"{'═'*70}")

    for round_num in range(1, MAX_ROUNDS + 1):
        review = agent.review_stitch_output(screenshot, design_specs, user_journeys)
        agent.print_stitch_review(review, round_num)

        if review["verdict"] == "PASS":
            print(f"\n  ✅ UI đạt chất lượng! Sẵn sàng for Dev.")
            break

        print(f"\n  📋 Sửa các điểm trên in Stitch, then chụp screenshot new.")
        new_path = input(f"  Path screenshot new (Enter to stop): ").strip()
        if not new_path:
            print("  ⏹  Stop review.")
            break
        screenshot = new_path
    else:
        print(f"\n  ⚠️  Done {MAX_ROUNDS} rounds — use UI current tại.")


def main():
    # ── --doctor (environment check) ─────────────────────────────────────────
    if "--doctor" in sys.argv:
        from core.doctor import main as doctor_main
        doctor_main()
        return

    # ── --trend (cross-session dashboard) ────────────────────────────────────
    if "--trend" in sys.argv:
        from reporting import build_trend_report
        from context import detect_project, MULTI_AGENT_DIRNAME
        from pathlib import Path as _P
        proj = detect_project(_P.cwd())
        if not proj:
            print("❌ No project found at CWD — cd into the project folder and try again.")
            sys.exit(1)
        sessions_dir = _P(proj.root) / MULTI_AGENT_DIRNAME / "sessions"
        out = _P(proj.root) / MULTI_AGENT_DIRNAME / "TREND.html"
        path = build_trend_report(sessions_dir, out, project_name=proj.name)
        print(f"📈 Trend report → {path}")
        print(f"   Open: open {path}")
        return

    print(BANNER)

    # ── --reselect-plan (standalone) ─────────────────────────────────────────
    if "--reselect-plan" in sys.argv and len(sys.argv) == 2:
        detect_budget(force_reselect=True)
        return

    # ── --review-ui SCREENSHOT --session SESSION_ID ──────────────────────────
    if "--review-ui" in sys.argv:
        screenshot = _get_arg("--review-ui")
        session_id = _get_arg("--session")
        profile    = _get_arg("--profile") or "default"
        if not screenshot or not session_id:
            print("❌ Usage: python main.py --review-ui screenshot.png --session <session_id>")
            sys.exit(1)
        _run_ui_review(screenshot, session_id, profile)
        return

    # ── --profiles ────────────────────────────────────────────────────────────
    if "--profiles" in sys.argv:
        profiles = _list_profiles()
        print("  📁 Available profiles:\n")
        for p in profiles:
            overrides = [f.stem for f in (RULES_DIR / p).glob("*.md")]
            tag = " (default)" if p == "default" else f" — overrides: {', '.join(overrides)}"
            print(f"  • {p}{tag}")
        return

    # ── --list ────────────────────────────────────────────────────────────────
    if "--list" in sys.argv:
        from context.project_context_reader import detect_project_name
        project_filter = _get_arg("--project") or detect_project_name(Path.cwd())
        sessions = ProductDevelopmentOrchestrator.list_sessions(project_name=project_filter)
        if not sessions:
            sessions = ProductDevelopmentOrchestrator.list_sessions()  # fallback: all
        if not sessions:
            print("  No resumable sessions found.")
        else:
            print(f"  📂 Resumable sessions (project: {project_filter}):\n")
            for s in sessions:
                done = ", ".join(s["completed"]) or "none"
                missing = ", ".join(s["missing"]) or "none"
                proj = s.get("project", "")
                proj_label = f"  [{proj}]" if proj else ""
                print(f"  • {s['session_id']}{proj_label}")
                print(f"    ✅ Done      : {done}")
                print(f"    ⏳ Remaining : {missing}\n")
        return

    # ── --profile ─────────────────────────────────────────────────────────────
    profile = _get_arg("--profile") or "default"
    available = _list_profiles()
    if profile not in available:
        print(f"❌ Profile '{profile}' not found. Available: {', '.join(available)}")
        sys.exit(1)

    # ── --resume SESSION_ID ───────────────────────────────────────────────────
    if "--resume" in sys.argv:
        session_id = _get_arg("--resume")
        if not session_id:
            print("❌ Session ID required. Use --list to see available sessions.")
            sys.exit(1)
        print(f"\n🔄 Resume session: {session_id}  (profile: {profile})\n")
        orchestrator = ProductDevelopmentOrchestrator(resume_session=session_id, profile=profile)
        orchestrator.run_resume()
        return

    # ── --feedback SESSION_ID ────────────────────────────────────────────────
    if "--feedback" in sys.argv:
        source_session = _get_arg("--feedback")
        if not source_session:
            print("❌ Session ID required. Use --list.")
            sys.exit(1)
        plan_name, token_budget = detect_budget(force_reselect="--reselect-plan" in sys.argv)
        orchestrator = ProductDevelopmentOrchestrator(profile=profile, token_budget=token_budget)
        feedback = _collect_feedback()
        if not feedback["description"]:
            print("❌ Feedback description required.")
            sys.exit(1)
        orchestrator.run_feedback(source_session, feedback)
        return

    # ── --update SESSION_ID ───────────────────────────────────────────────────
    if "--update" in sys.argv:
        source_session = _get_arg("--update")
        if not source_session:
            print("❌ Source session ID required. Use --list.")
            sys.exit(1)
        non_flag = [a for a in sys.argv[1:] if not a.startswith("--")
                    and a not in (source_session, _get_arg("--profile") or "")]
        if non_flag:
            task = _resolve_input(" ".join(non_flag))
        else:
            print("  Enter the change to apply to the existing session:")
            task = input("  > ").strip()
        if not task:
            print("❌ Task description required.")
            sys.exit(1)
        plan_name, token_budget = detect_budget()
        orchestrator = ProductDevelopmentOrchestrator(profile=profile, token_budget=token_budget)
        orchestrator.run_update(task, source_session)
        return

    # ── Normal run ────────────────────────────────────────────────────────────
    non_flag_args = [a for a in sys.argv[1:] if not a.startswith("--") and a != _get_arg("--profile")]
    if non_flag_args:
        product_idea = _resolve_input(" ".join(non_flag_args))
    else:
        product_idea = _prompt_user_input()

    if not product_idea:
        print("❌ Please enter a task.")
        sys.exit(1)

    maintain_dir = _get_arg("--maintain")
    if maintain_dir and not Path(maintain_dir).exists():
        print(f"❌ --maintain path does not exist: {maintain_dir}")
        sys.exit(1)

    # ── Token budget: CLI override → auto-detect plan ────────────────────────
    budget_arg = _get_arg("--budget")
    force_reselect = "--reselect-plan" in sys.argv
    if budget_arg and budget_arg.isdigit():
        token_budget = int(budget_arg)
        plan_name    = "custom"
    else:
        plan_name, token_budget = detect_budget(force_reselect=force_reselect)

    from context.project_context_reader import detect_project_name
    project_name = detect_project_name(maintain_dir or Path.cwd())
    mode = f"maintain ({maintain_dir})" if maintain_dir else "new project"
    print(f"\n⚙️  Starting pipeline... (profile: {profile}, mode: {mode})")
    print(f"  Plan: {plan_name}  |  Budget: {token_budget:,}  |  Project: {project_name}  |  Outputs: outputs/{project_name}/\n")

    orchestrator = ProductDevelopmentOrchestrator(
        profile=profile, maintain_dir=maintain_dir, token_budget=token_budget
    )

    resources = None
    dev_slots    = _get_arg("--dev-slots")
    sprint_hours = _get_arg("--sprint-hours")
    if dev_slots or sprint_hours:
        resources = {
            "dev_slots":     int(dev_slots) if dev_slots else 2,
            "sprint_hours":  int(sprint_hours) if sprint_hours else 80,
            "sprints_ahead": 3,
        }
    orchestrator.run(product_idea, resources=resources)


if __name__ == "__main__":
    main()
