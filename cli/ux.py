"""
UX helpers for the mag CLI:

- status_report(profile)        → human-readable profile health snapshot
- load_config(profile)          → merge .mag.yaml into env vars (side-effect)
- undo_session(session_id, ...) → revert rule-file changes from one session
- show_diff_and_confirm(path, new_content)
                                 → unified diff + [Y/n] prompt
- chat_loop()                   → multi-turn pre-flight CLI

Everything lives here so main.py stays a thin dispatcher and the
orchestrator/agent layers don't get contaminated with CLI concerns.
"""
from __future__ import annotations
import re
import difflib
import json
import os
import sys
from datetime import datetime
from pathlib import Path


# ── ANSI helpers (no extra deps) ──────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


BOLD   = lambda s: _c("1",  s)
DIM    = lambda s: _c("2",  s)
RED    = lambda s: _c("31", s)
GREEN  = lambda s: _c("32", s)
YELLOW = lambda s: _c("33", s)
BLUE   = lambda s: _c("34", s)
MAGENTA = lambda s: _c("35", s)
CYAN   = lambda s: _c("36", s)


# ── Status report ────────────────────────────────────────────────────────────

def status_report(profile: str = "default", rules_dir: Path | None = None) -> str:
    """
    Render a multi-section status report for one profile.
    Returns the full text — caller prints it.
    """
    if rules_dir is None:
        from agents.base_agent import _RULES_DIR
        rules_dir = _RULES_DIR

    profile_dir = Path(rules_dir) / profile
    lines: list[str] = []

    lines.append(BOLD(CYAN(f"═══ mag status — profile: {profile} ═══")))
    lines.append("")

    # ── Active shadow A/B tests ──────────────────────────────────────────────
    lines.append(BOLD(YELLOW("🧪 Active shadow A/B tests")))
    try:
        from learning.rule_evolver import (
            ShadowLog, SHADOW_MIN_SESSIONS, SHADOW_PROMOTE_DELTA,
        )
        sl = ShadowLog(profile_dir)
        variants = sl._data.get("variants", {})
        if not variants:
            lines.append(DIM("  (none)"))
        else:
            for k, v in variants.items():
                nb = len(v.get("baseline", []))
                ns = len(v.get("shadow",   []))
                need_b = max(0, SHADOW_MIN_SESSIONS - nb)
                need_s = max(0, SHADOW_MIN_SESSIONS - ns)
                if nb >= SHADOW_MIN_SESSIONS and ns >= SHADOW_MIN_SESSIONS:
                    avg_b = sum(x["score"] for x in v["baseline"]) / nb
                    avg_s = sum(x["score"] for x in v["shadow"])   / ns
                    delta = avg_s - avg_b
                    label = (GREEN("PROMOTE eligible")  if delta >=  SHADOW_PROMOTE_DELTA
                             else RED("DEMOTE eligible") if delta <= -SHADOW_PROMOTE_DELTA
                             else DIM("continue"))
                    lines.append(
                        f"  {k}   baseline avg {avg_b:.2f} (n={nb}) "
                        f"vs shadow {avg_s:.2f} (n={ns})  Δ={delta:+.2f}  → {label}"
                    )
                else:
                    lines.append(
                        f"  {k}   baseline n={nb}, shadow n={ns}  "
                        + DIM(f"(need {need_b} more baseline, {need_s} more shadow)")
                    )
    except Exception as e:
        lines.append(DIM(f"  (shadow log unavailable: {e})"))

    lines.append("")

    # ── IntegrityRules alerts ─────────────────────────────────────────────────
    lines.append(BOLD(YELLOW("🚨 IntegrityRules alerts")))
    try:
        from learning.integrity_rules import (
            IntegrityRules, MODULE_BLACKLIST_THRESHOLD,
        )
        ir = IntegrityRules(profile_dir)
        shown = False
        for mod, count in sorted(ir.module_blacklist.items(), key=lambda x: -x[1]):
            if count >= MODULE_BLACKLIST_THRESHOLD:
                lines.append(f"  {RED('⚠')}  module {BOLD(mod)}: {count} failures → force Critic")
                shown = True
            elif count > 0:
                lines.append(DIM(f"     module {mod}: {count} failures (below force threshold)"))
        for kw, risk in sorted(ir.keyword_risk.items()):
            level_color = RED if risk == "high" else YELLOW if risk == "med" else DIM
            lines.append(f"  {level_color('●')}  keyword {BOLD(repr(kw))}: risk={risk}")
            shown = True
        if not shown:
            lines.append(DIM("  (clean — no forced-Critic triggers)"))
    except Exception as e:
        lines.append(DIM(f"  (integrity rules unavailable: {e})"))

    lines.append("")

    # ── Agent reputation ─────────────────────────────────────────────────────
    lines.append(BOLD(YELLOW("🧠 Agent reputation")))
    try:
        from learning.integrity_rules import IntegrityRules
        ir = IntegrityRules(profile_dir)
        if not ir.agent_reputation:
            lines.append(DIM("  (no false-negatives recorded — all clean)"))
        else:
            for role, rep in sorted(ir.agent_reputation.items()):
                fn = int(rep.get("false_negatives", 0))
                window = int(rep.get("force_window_remaining", 0))
                flag = (RED(f"force window {window} remaining")
                        if window > 0 else DIM("clean"))
                lines.append(f"  {role:10s}  fn={fn}   {flag}")
    except Exception as e:
        lines.append(DIM(f"  (reputation unavailable: {e})"))

    lines.append("")

    # ── Recent sessions ──────────────────────────────────────────────────────
    lines.append(BOLD(YELLOW("📊 Last 5 sessions")))
    try:
        from context import MULTI_AGENT_DIRNAME, detect_project
        from pathlib import Path as _P
        proj = detect_project(_P.cwd())
        if proj:
            sessions_dir = _P(proj.root) / MULTI_AGENT_DIRNAME / "sessions"
            if sessions_dir.exists():
                dirs = sorted(
                    [d for d in sessions_dir.iterdir() if d.is_dir()],
                    key=lambda d: d.name,
                    reverse=True,
                )[:5]
                for d in dirs:
                    kind = ""
                    pm_path = d / f"{d.name}_pm.md"
                    if pm_path.exists():
                        text = pm_path.read_text(encoding="utf-8", errors="ignore")
                        m = re.search(r"\*\*Kind\*\*:\s*`([a-z_]+)`", text)
                        if m:
                            kind = m.group(1)
                    lines.append(f"  {d.name:30s}  kind={kind or '?':12s}")
            else:
                lines.append(DIM("  (no sessions/ directory yet)"))
        else:
            lines.append(DIM("  (no project detected at cwd)"))
    except Exception as e:
        lines.append(DIM(f"  (session listing unavailable: {e})"))

    lines.append("")

    # ── Cost history ─────────────────────────────────────────────────────────
    lines.append(BOLD(YELLOW("💰 Cost history (last 5 ratios)")))
    cost_path = profile_dir / ".learning" / "cost_history.json"
    if cost_path.exists():
        try:
            data = json.loads(cost_path.read_text(encoding="utf-8"))
            if not data:
                lines.append(DIM("  (empty)"))
            else:
                for agent, ratios in sorted(data.items()):
                    recent = ratios[-5:] if ratios else []
                    if not recent:
                        continue
                    avg = sum(recent) / len(recent)
                    mark = (RED("over budget trend")   if avg >= 1.5
                            else YELLOW("watch")         if avg >= 1.2
                            else GREEN("within budget"))
                    lines.append(f"  {agent:10s}  avg={avg:.2f}×   {mark}")
        except Exception as e:
            lines.append(DIM(f"  (cost history unreadable: {e})"))
    else:
        lines.append(DIM("  (no cost_history.json yet)"))

    lines.append("")

    # ── Active rule shadows on disk ──────────────────────────────────────────
    lines.append(BOLD(YELLOW("📂 Rule artefacts on disk")))
    shadows  = list(profile_dir.glob("*.shadow.md"))
    rejected = list(profile_dir.glob("*.rejected.md"))
    lines.append(f"  shadow rule files : {len(shadows)}  ({', '.join(p.stem for p in shadows) or '-'})")
    lines.append(f"  rejected archives : {len(rejected)}")
    feedback_dir = profile_dir / ".feedback"
    fb_count = 0
    if feedback_dir.exists():
        fb_count = sum(1 for p in feedback_dir.glob("*.jsonl"))
    lines.append(f"  feedback entries  : {fb_count} files")

    lines.append("")
    lines.append(DIM(f"Profile dir: {profile_dir}"))

    return "\n".join(lines)


# ── Central YAML config ──────────────────────────────────────────────────────

# Keys recognised in .mag.yaml → env var mapping
_YAML_TO_ENV = {
    "pipeline.flow":                        "MULTI_AGENT_FLOW",
    "pipeline.max_concurrent":              "MULTI_AGENT_MAX_CONCURRENT",
    "pipeline.call_spacing_ms":             "MULTI_AGENT_CALL_SPACING_MS",
    "pipeline.auto_commit":                 "MULTI_AGENT_AUTO_COMMIT",
    "pipeline.no_auto_feedback":            "MULTI_AGENT_NO_AUTO_FEEDBACK",
    "pipeline.auto_heal":                   "MULTI_AGENT_AUTO_HEAL",
    "pipeline.skill_review":                "MULTI_AGENT_SKILL_REVIEW",
    "critic.all":                           "MULTI_AGENT_CRITIC_ALL",
    "critic.tl.always":                     "MULTI_AGENT_TL_CRITIC_ALWAYS",
    "critic.tl.never":                      "MULTI_AGENT_TL_CRITIC_NEVER",
    "learning.legacy_rule_optimizer":       "MULTI_AGENT_LEGACY_RULE_OPTIMIZER",
    "skill.llm_pick":                       "MULTI_AGENT_SKILL_LLM",
    "skill.max":                            "MULTI_AGENT_SKILL_MAX",
    "stitch.max_retries":                   "STITCH_MAX_RETRIES",
    "stitch.rate_limit_sec":                "STITCH_RATE_LIMIT_SEC",
    "stitch.headless":                      "STITCH_HEADLESS",
}


def _flatten_yaml(d: dict, prefix: str = "") -> dict:
    """Nested dict → dotted key dict."""
    flat: dict = {}
    for k, v in (d or {}).items():
        new_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_yaml(v, new_key))
        else:
            flat[new_key] = v
    return flat


def load_config(search_paths: list[Path] | None = None) -> dict:
    """
    Locate and load .mag.yaml, apply values as env vars (if not already set
    explicitly). Returns {mapped_env_var: value} for logging.

    Search order:
        1. ./.mag.yaml
        2. $PWD/.mag.yaml (same as above, kept for clarity)
        3. ~/.mag.yaml
    Explicit env vars always win — they're already in os.environ and we
    only set keys that aren't present yet.
    """
    if search_paths is None:
        search_paths = [
            Path.cwd() / ".mag.yaml",
            Path.home() / ".mag.yaml",
        ]

    path = next((p for p in search_paths if p.exists()), None)
    if path is None:
        return {}

    try:
        import yaml  # type: ignore
    except ImportError:
        # Fallback minimal YAML parser for the simple key: value format we use.
        return _load_yaml_minimal(path)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"  ⚠️  Could not parse {path}: {e}")
        return {}

    applied: dict = {}
    for key, env in _YAML_TO_ENV.items():
        val = _dig(data, key)
        if val is None:
            continue
        if env in os.environ:
            continue  # explicit env wins
        os.environ[env] = _stringify(val)
        applied[env] = os.environ[env]

    if applied:
        print(f"  ⚙️  Loaded {len(applied)} config value(s) from {path}")
    return applied


def _dig(d: dict, dotted: str):
    """Fetch a nested value from a dict via dotted key. None if missing."""
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _stringify(v) -> str:
    """Coerce YAML values to env-var-safe strings."""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _load_yaml_minimal(path: Path) -> dict:
    """
    Tiny fallback parser for simple nested YAML when pyyaml isn't installed.
    Handles:
      top:
        key: value
        nested:
          key: value
    That's enough for the config keys we care about.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return {}

    stack: list[tuple[int, dict]] = [(-1, {})]
    root = stack[0][1]
    applied: dict = {}

    for line in raw.splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        indent = len(stripped) - len(stripped.lstrip())
        content = stripped.strip()
        if ":" not in content:
            continue
        key, _, val = content.partition(":")
        key, val = key.strip(), val.strip()

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]

        if not val:
            new_node: dict = {}
            parent[key] = new_node
            stack.append((indent, new_node))
        else:
            # Strip inline comments.
            if "#" in val:
                val = val.split("#", 1)[0].strip()
            # Strip matching quotes.
            if (val.startswith("'") and val.endswith("'")) or \
               (val.startswith('"') and val.endswith('"')):
                val = val[1:-1]
            parent[key] = val

    # Now apply to env like the main path.
    for key, env in _YAML_TO_ENV.items():
        v = _dig(root, key)
        if v is None or env in os.environ:
            continue
        os.environ[env] = _stringify(v)
        applied[env] = os.environ[env]

    if applied:
        print(f"  ⚙️  Loaded {len(applied)} config value(s) from {path} (minimal parser)")
    return applied


# ── Diff preview ─────────────────────────────────────────────────────────────

def show_diff_and_confirm(rule_path: Path, new_content: str,
                            auto_yes: bool = False) -> bool:
    """Show a unified diff of the pending change and prompt Y/n.

    Returns True if change approved (or auto_yes=True), False otherwise.
    """
    rule_path = Path(rule_path)
    old_content = rule_path.read_text(encoding="utf-8") if rule_path.exists() else ""
    if old_content == new_content:
        return True  # nothing to do

    diff = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{rule_path.name}",
        tofile=f"b/{rule_path.name}",
        lineterm="",
    )
    diff_text = "".join(diff)

    print("\n" + BOLD(CYAN(f"── Proposed change to {rule_path} ──")))
    # Colourise diff lines.
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            print(BOLD(line))
        elif line.startswith("+"):
            print(GREEN(line))
        elif line.startswith("-"):
            print(RED(line))
        elif line.startswith("@@"):
            print(CYAN(line))
        else:
            print(line)
    print(BOLD(CYAN("────")) + "\n")

    if auto_yes:
        print(DIM("  [--yes] auto-applying"))
        return True

    while True:
        ans = input("Apply this change? [Y/n/view-again]: ").strip().lower()
        if ans in ("", "y", "yes"):
            return True
        if ans in ("n", "no"):
            print(DIM("  Skipped."))
            return False
        if ans in ("v", "view", "view-again"):
            # Re-show without re-asking.
            return show_diff_and_confirm(rule_path, new_content, auto_yes=False)


# ── Undo ─────────────────────────────────────────────────────────────────────

def undo_session(session_id: str, profile: str = "default",
                  rules_dir: Path | None = None) -> dict:
    """Revert rule-file changes made during `session_id`.

    Strategy: RuleEvolver writes to `<rules>/<profile>/backups/<filename>.<ts>.bak`
    before every mutation. We scan the backup dir for files created during
    the session window (± 2 hours from session_id parse) and restore them.

    Returns {restored: [paths], missing: [reason]}.
    """
    if rules_dir is None:
        from agents.base_agent import _RULES_DIR
        rules_dir = _RULES_DIR
    profile_dir = Path(rules_dir) / profile
    backup_dir  = profile_dir / "backups"

    # Parse session timestamp from session_id (format YYYYMMDD_HHMMSS_hex)
    try:
        ts_part = "_".join(session_id.split("_")[:2])
        session_ts = datetime.strptime(ts_part, "%Y%m%d_%H%M%S")
    except ValueError:
        return {"restored": [], "missing": [f"unparseable session id: {session_id}"]}

    if not backup_dir.exists():
        return {"restored": [], "missing": [f"no backup dir at {backup_dir}"]}

    restored: list[str] = []
    # Window: 30 min before session start → 4h after (rule optimiser runs at end)
    low  = session_ts.timestamp() - 30 * 60
    high = session_ts.timestamp() + 4  * 3600

    for bak in sorted(backup_dir.glob("*.bak")):
        mtime = bak.stat().st_mtime
        if mtime < low or mtime > high:
            continue
        # Original name: <filename>.<ts>.bak → filename is everything before last two dots.
        parts = bak.name.rsplit(".", 2)
        if len(parts) < 3:
            continue
        orig_name = parts[0]
        orig_path = profile_dir / orig_name if orig_name.endswith(".md") \
                    else profile_dir / orig_name
        if not orig_path.exists():
            # Try criteria subdir.
            alt = profile_dir / "criteria" / orig_name
            if alt.exists():
                orig_path = alt
        try:
            orig_path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            restored.append(str(orig_path))
        except Exception as e:
            print(f"  ⚠️  failed to restore {orig_path}: {e}")

    return {"restored": restored,
            "window":   f"{datetime.fromtimestamp(low):%Y-%m-%d %H:%M} → "
                        f"{datetime.fromtimestamp(high):%Y-%m-%d %H:%M}",
            "missing":  [] if restored else ["no backups matched the session window"]}


# ── Shadow A/B status report ─────────────────────────────────────────────────

def shadow_status_report(profile: str = "default", rules_dir: Path | None = None) -> str:
    """Render the live shadow A/B log: active variants + sample counts + delta
    + verdict (promote / demote / continue).

    This is the observability surface for rule A/B testing. Users ran
    `mag shadow-status [--profile X]` to see whether anything the learning
    loop registered as "shadowed" is converging.
    """
    if rules_dir is None:
        from core.paths import RULES_DIR
        rules_dir = RULES_DIR
    profile_dir = Path(rules_dir) / profile
    log_path    = profile_dir / ".shadow_log.json"

    out: list[str] = []
    out.append(BOLD(CYAN(f"\n🧪  Shadow A/B status — profile={profile}")))
    out.append(DIM("   file: " + str(log_path)))

    if not log_path.exists():
        out.append(YELLOW("\n   No shadow log yet — no rule variants have been "
                           "registered for A/B."))
        out.append(DIM("   Tip: rules enter shadow when RuleEvolver scores them "
                        "between auto-apply and pending thresholds."))
        return "\n".join(out) + "\n"

    # Lazy import so this module stays import-light for non-learning CLI paths.
    try:
        from learning.rule_evolver import ShadowLog
        log = ShadowLog(profile_dir)
    except (ImportError, ValueError, AttributeError) as e:
        out.append(RED(f"\n   Failed to load shadow log: {e}"))
        return "\n".join(out) + "\n"

    verdicts = log.verdicts()
    variants = log._data.get("variants", {})     # noqa: SLF001 — read-only

    if not variants:
        out.append(YELLOW("\n   0 active shadow variants."))
        return "\n".join(out) + "\n"

    # ── Summary line ────────────────────────────────────────────────────────
    n_promote  = sum(1 for v in verdicts if v["verdict"] == "promote")
    n_demote   = sum(1 for v in verdicts if v["verdict"] == "demote")
    n_continue = sum(1 for v in verdicts if v["verdict"] == "continue")
    n_waiting  = len(variants) - len(verdicts)
    out.append("")
    out.append(f"   Variants: {BOLD(str(len(variants)))}  "
                f"│ promote={GREEN(str(n_promote))}  "
                f"demote={RED(str(n_demote))}  "
                f"continue={YELLOW(str(n_continue))}  "
                f"waiting={DIM(str(n_waiting))}")
    out.append("")

    # ── Detail per variant ──────────────────────────────────────────────────
    verdict_by_key = {v["key"]: v for v in verdicts}
    for key, v in sorted(variants.items()):
        n_b = len(v.get("baseline", []))
        n_s = len(v.get("shadow",   []))
        created = v.get("created_at", "-")
        out.append(f"   ┌─ {BOLD(key)}  (created {created})")
        out.append(f"   │  shadow_path: {DIM(v.get('shadow_path', '-'))}")
        out.append(f"   │  samples   : baseline n={n_b}  │  shadow n={n_s}")

        verdict = verdict_by_key.get(key)
        if verdict is None:
            out.append(DIM(f"   │  verdict   : (waiting — need more samples)"))
        else:
            color = (GREEN if verdict["verdict"] == "promote"
                     else RED if verdict["verdict"] == "demote"
                     else YELLOW)
            avg_b = verdict["baseline_avg"]
            avg_s = verdict["shadow_avg"]
            delta = verdict["delta"]
            out.append(f"   │  verdict   : {color(BOLD(verdict['verdict'].upper()))}")
            out.append(f"   │  baseline  : avg={avg_b:.2f}  std={verdict['baseline_std']:.2f}")
            out.append(f"   │  shadow    : avg={avg_s:.2f}  std={verdict['shadow_std']:.2f}")
            sign = "+" if delta >= 0 else ""
            out.append(f"   │  delta     : {sign}{delta:.2f}  "
                        f"(sig threshold {verdict['sig_threshold']:.2f})")
            if verdict.get("reject_reason"):
                out.append(DIM(f"   │  note      : {verdict['reject_reason']}"))
        out.append(f"   └─")
    out.append("")
    return "\n".join(out)


# ── Interactive chat mode ────────────────────────────────────────────────────

def chat_loop(run_pipeline_fn) -> None:
    """Multi-turn CLI that walks the user through a request before firing
    the pipeline.

    run_pipeline_fn(product_idea, resources) → result dict
    Caller provides the actual orchestrator wrapper so this module stays
    import-light.
    """
    print(BOLD(CYAN("\n🗨️   mag chat — interactive mode. Type 'quit' to exit.")))
    print(DIM("     Each turn: describe a task, I'll classify it, then confirm + run.\n"))

    while True:
        try:
            task = input(BOLD(GREEN("▸ ")) + "What do you want to build/fix? ").strip()
        except EOFError:
            print()
            return
        if not task or task.lower() in ("quit", "exit", "q"):
            print(DIM("bye."))
            return

        # Classify up front so the user sees what PM sẽ làm.
        try:
            from agents.pm_agent import PMAgent
            pm = PMAgent(profile=os.environ.get("MAG_PROFILE", "default"))
            decision = pm._heuristic(task) or pm.classify(task)
        except Exception as e:
            print(DIM(f"  (classifier unavailable: {e})"))
            decision = None

        if decision:
            conf_color = (GREEN if decision.confidence >= 0.85
                          else YELLOW if decision.confidence >= 0.6
                          else RED)
            print(f"  🧭 PM suggests kind = "
                  f"{BOLD(decision.kind)}  "
                  f"(confidence {conf_color(f'{decision.confidence:.2f}')})")
            if decision.reason:
                print(DIM(f"     reason: {decision.reason[:100]}"))

            if decision.confidence < 0.6:
                print("  ⚠️  Low confidence — pipeline will ask you to confirm kind.")

        ans = input(BOLD(GREEN("▸ ")) + "Run this through the pipeline? [Y/n/edit]: ").strip().lower()
        if ans in ("n", "no"):
            continue
        if ans in ("e", "edit"):
            more = input(BOLD(GREEN("▸ ")) + "Add context / clarify: ").strip()
            if more:
                task = f"{task}\n\nAdditional context:\n{more}"

        # Fire.
        try:
            run_pipeline_fn(task, None)
        except KeyboardInterrupt:
            print(DIM("\n  interrupted."))
            continue
        except Exception as e:
            print(RED(f"  pipeline error: {e}"))
            continue

        # Post-session feedback prompt.
        rating_raw = input(BOLD(GREEN("▸ ")) + "Rate the session [1-5, enter=skip]: ").strip()
        if rating_raw.isdigit() and 1 <= int(rating_raw) <= 5:
            comment = input(BOLD(GREEN("▸ ")) + "Comment (optional): ").strip()
            # Find latest session id — lazy import to avoid circular.
            try:
                # We don't have the instance here; fall back to telling user the CLI.
                print(DIM("  Run: mag feedback <session_id> --agent general "
                          f"--rating {rating_raw} --comment \"{comment}\""))
            except (ImportError, ValueError, AttributeError):
                pass

        print()
