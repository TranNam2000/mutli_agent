"""
Multi-Agent Orchestrator with real agent-to-agent communication + parallel execution.

Pipeline:
  BA ──► Design ┐  (parallel)
        TechLead ┘ ──► Dev ←→ TechLead ──► QA ←→ Dev
"""
import os
import shutil
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from core.message_bus import MessageBus
from agents import BAAgent, DesignAgent, TechLeadAgent, DevAgent, TestAgent, CriticAgent, RuleOptimizerAgent, InvestigationAgent, SkillDesignerAgent, PMAgent
from agents.pm_agent import ALL_KINDS as PM_ALL_KINDS, RouteDecision
from context.project_context_reader import save_context
from context.context_builder import ContextBuilder
from context import session_file
from core.token_tracker import TokenTracker


# Thread-safe print
_print_lock = threading.Lock()

def tprint(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


# ── Context management ────────────────────────────────────────────────────────

def _smart_trim(text: str, max_chars: int, keep_headers: bool = True) -> str:
    """
    Smart truncation: keeps markdown headers and first paragraph of each section.
    Falls back to simple truncation when text is short enough.
    """
    if len(text) <= max_chars:
        return text

    if not keep_headers:
        return text[:max_chars] + "\n\n...[truncated]"

    lines = text.splitlines()
    result = []
    budget = max_chars

    for line in lines:
        entry = line + "\n"
        if len(entry) > budget:
            if budget > 40:
                result.append(line[:budget] + "...")
            break
        result.append(entry)
        budget -= len(entry)
        if budget <= 0:
            break

    return "".join(result)


def _extract_section(text: str, *keywords: str, max_chars: int = 800) -> str:
    """Extract the first section whose header matches any keyword."""
    lines = text.splitlines()
    collecting = False
    result = []
    budget = max_chars

    for line in lines:
        stripped = line.lstrip("#").strip().lower()
        if any(kw.lower() in stripped for kw in keywords):
            collecting = True
        elif line.startswith("#") and collecting:
            break  # next section — stop

        if collecting:
            result.append(line)
            budget -= len(line) + 1
            if budget <= 0:
                result.append("...[truncated]")
                break

    return "\n".join(result) if result else _smart_trim(text, max_chars)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class ProductDevelopmentOrchestrator:
    PIPELINE = [
        ("pm",        "🧭 PM Routing",             "classifying request..."),
        ("ba",        "📋 Business Analysis",     "analyzing requirements..."),
        ("design",    "🎨 UI/UX Design",           "designing screens & components..."),
        ("techlead",  "🏗️  Technical Architecture", "designing system & API..."),
        ("test_plan", "📝 Test Planning",          "writing test cases from requirements..."),
        ("dev",       "💻 Implementation",         "writing production code..."),
        ("test",      "🧪 QA Review",              "verifying implementation against test plan..."),
    ]
    STEP_KEYS = ["pm", "ba", "design", "techlead", "test_plan", "dev", "test"]

    # Steps that go through Critic scoring + revise loop. Other steps skip
    # Critic entirely to save ~30–40% of Critic LLM calls per session.
    # Override via env var `MULTI_AGENT_CRITIC_ALL=1` to restore legacy behavior.
    CRITIC_STEPS = frozenset({"dev", "test"})

    def __init__(self, output_dir: str = "outputs", resume_session: str | None = None, profile: str = "default", maintain_dir: str | None = None, token_budget: int = 500_000):
        from context.project_context_reader import detect_project_name
        from context import resolve_output_dir
        self._output_dir_base = output_dir
        project_name = detect_project_name(maintain_dir or Path.cwd())
        self.project_name = project_name

        # NEW: prefer in-project output (.multi_agent/sessions/) when maintain_dir given
        fallback = Path(output_dir) / project_name
        self.output_dir = resolve_output_dir(maintain_dir, fallback)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.project_info = None          # populated by _init_project_info later
        self.git_helper   = None          # GitHelper instance when in maintain mode
        self.git_snapshot = None          # baseline snapshot before pipeline runs
        self.health_report = None         # HealthReport pre-flight

        self.results: dict[str, str] = {}
        self.bus = MessageBus()
        self.profile = profile

        if resume_session:
            self.session_id = resume_session
            self._load_checkpoints()
        else:
            # Timestamp + short random suffix → no collision between concurrent runs
            import secrets
            self.session_id = (
                datetime.now().strftime("%Y%m%d_%H%M%S")
                + "_" + secrets.token_hex(2)   # 4 hex chars = 65k possibilities / second
            )

        self.agents: dict[str, object] = {
            "pm":       PMAgent(profile=profile),
            "ba":       BAAgent(profile=profile),
            "design":   DesignAgent(profile=profile),
            "techlead": TechLeadAgent(profile=profile),
            "dev":      DevAgent(profile=profile),
            "test":     TestAgent(profile=profile),
        }
        self.critic = CriticAgent(profile=profile)
        self.rule_optimizer = RuleOptimizerAgent(profile=profile)
        self.investigator = InvestigationAgent(profile=profile)
        self.skill_designer = SkillDesignerAgent(profile=profile)
        self.critic_reviews: list[dict] = []
        self.maintain_mode = bool(maintain_dir)
        self._maintain_dir: str | None = maintain_dir  # saved for task_hint reload in run()
        self.tokens = TokenTracker(budget=token_budget)
        # ── Audit / Emergency Audit Mode state ──────────────────────────────
        # AuditLog is lazily constructed the first time we actually need to
        # record a false-negative (so tests instantiating the orchestrator
        # don't create stray log files).
        self._audit_log = None  # type: ignore[var-annotated]
        # task_id → list of roles whose Critic was skipped for this task.
        self._skipped_critic_by_task: dict[str, list[str]] = {}
        # task_id → predicted metadata snapshot at skip time.
        self._skip_snapshot: dict[str, dict] = {}
        # Once set, forces Critic on regardless of metadata — triggered by QA fail.
        self._emergency_audit: bool = False
        # Integrity rules — persistent across sessions. Loaded now so
        # "yesterday's audit findings" actively gate today's skip decisions.
        from learning.integrity_rules import IntegrityRules
        from agents.base_agent import _RULES_DIR
        self._integrity = IntegrityRules(_RULES_DIR / profile)
        all_agents = list(self.agents.values()) + [self.critic, self.rule_optimizer,
                                                    self.investigator, self.skill_designer]
        for agent in all_agents:
            agent.message_bus = self.bus
            agent.token_tracker = self.tokens

        # ── Maintain mode: inject existing project context into agents ────────
        if maintain_dir:
            self._load_project_context(maintain_dir)
        elif not maintain_dir:
            self._auto_detect_maintain()

    # ── Maintain mode ─────────────────────────────────────────────────────────

    def _auto_detect_maintain(self):
        """Auto-detect maintain mode when working directory has recognizable project files."""
        PIPELINE_DIR = Path(__file__).resolve().parent
        # Mobile/web project signals — intentionally excludes requirements.txt / pyproject.toml
        # so running from inside the pipeline dir itself never triggers maintain mode.
        signals   = ["pubspec.yaml", "package.json", "build.gradle", "pom.xml",
                     "Cargo.toml", "go.mod"]
        code_dirs = ["lib", "src", "app", "packages"]

        # Search cwd first, then parent dirs (up to 3 levels) — skipping the pipeline dir itself
        search_paths = []
        candidate = Path.cwd().resolve()
        for _ in range(4):
            if candidate != PIPELINE_DIR:
                search_paths.append(candidate)
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent

        for path in search_paths:
            has_signal = any((path / s).exists() for s in signals)
            has_code   = any((path / d).is_dir() for d in code_dirs)
            if has_signal or has_code:
                tprint(f"\n  🔍 Auto-detected project in {path} — activating maintain mode")
                self._maintain_dir = str(path)
                # Update project name & output dir to reflect the real project
                from context.project_context_reader import detect_project_name
                self.project_name = detect_project_name(path)
                self.output_dir = Path(self._output_dir_base) / self.project_name
                self.output_dir.mkdir(parents=True, exist_ok=True)
                self._load_project_context(str(path))
                return

    def _detect_maintain_from_task(self, task: str):
        """Detect maintain mode from task description keywords (fix, bug, investigate, etc.)."""
        maintain_keywords = [
            "fix", "bug", "bug", "fix", "investigate", "investigate", "debug",
            "broken", "not working", "doesn't work", "not working",
            "update existing", "update", "refactor", "regression",
            "crash", "error in", "issue with", "problem with",
        ]
        task_lower = task.lower()
        matched = [kw for kw in maintain_keywords if kw in task_lower]
        if matched:
            tprint(f"\n  🔍 Task keywords {matched[:2]} suggest maintain mode — checking cwd...")
            self._auto_detect_maintain()

    def _load_project_context(self, maintain_dir: str, task_hint: str = ""):
        """
        Maintain-mode setup:
          1. Detect project (handles monorepos, picks right subproject)
          2. Build SCOPED context (keyword-driven, not blindly reading 60 files)
          3. Initialize Git helper + branch
          4. Run health check to capture baseline
          5. Save context inside .multi_agent/sessions/<id>/
        """
        from context import (
            detect_project, build_scoped_context, GitHelper, HealthChecker,
            session_file,
        )

        tprint(f"\n  🔍 Reading project context from: {maintain_dir}")
        if task_hint:
            tprint(f"  🎯 Task hint: \"{task_hint[:60]}\" — scoped reading")

        # 1. Detect project
        self.project_info = detect_project(maintain_dir, task_hint=task_hint)
        if self.project_info:
            if self.project_info.is_monorepo:
                tprint(f"  🌳 Monorepo detected: {len(self.project_info.subprojects)} subprojects")
            tprint(f"  📦 Project: {self.project_info}")

        # 2. Scoped context
        project = self.project_info
        if project and task_hint:
            context = build_scoped_context(project, task_hint)
            strategy = "scoped (keyword-driven)"
        else:
            # Fallback to full scan when no task hint yet
            context_path = session_file(self.output_dir, self.session_id, "inputs",
                                         extension="tmp")
            context = save_context(
                project.root if project else maintain_dir,
                context_path, task_hint=task_hint,
            )
            context_path.unlink(missing_ok=True)
            strategy = "full scan"

        # Save context file inside session folder
        ctx_path = session_file(self.output_dir, self.session_id,
                                 "inputs", extension="md")
        ctx_path.write_text(
            f"# Project Context (strategy: {strategy})\n\n{context}",
            encoding="utf-8",
        )
        tprint(f"  📄 Context [{strategy}] → {ctx_path.name} ({len(context):,} chars)")

        # 3. Git integration
        if project:
            self.git_helper = GitHelper(project.root)
            if self.git_helper.is_repo():
                self.git_snapshot = self.git_helper.snapshot(
                    self.session_id, create_branch=True,
                )
                tprint(f"  🌿 Git branch: {self.git_snapshot.created_branch or self.git_snapshot.branch}")
                if self.git_snapshot.dirty_files:
                    tprint(f"  ⚠️  {len(self.git_snapshot.dirty_files)} files uncommitted "
                           f"before pipeline start — will not commit")

        # 4. Health check baseline (skip on resume — already done last time)
        if project and not self.results:
            try:
                checker = HealthChecker(project, timeout_s=60)
                self.health_report = checker.run(skip_tests=False)
                HealthChecker.print_report(self.health_report)
                # Cache baseline so Dev can diff later
                from context import resolve_cache_dir
                import json as _json
                cache = resolve_cache_dir(project.root)
                (cache / "health_baseline.json").write_text(
                    _json.dumps({
                        "session":          self.session_id,
                        "errors":           self.health_report.analyze_errors,
                        "warnings":         self.health_report.analyze_warnings,
                        "test_failed":      self.health_report.test_failed,
                        "test_passed":      self.health_report.test_passed,
                        "baseline_issues":  list(self.health_report.baseline_issues),
                    }, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                tprint(f"  ⚠️  Health check skipped: {e}")

        # 5. Inject context into agents
        for key in ["ba", "techlead", "dev", "test"]:
            self.agents[key].project_context = context
        self.investigator.project_context = context
        self.maintain_mode = True
        if project:
            self._maintain_dir = str(project.root)

        # 6. Start context refresher for long-running sessions
        if project:
            from context import ContextRefresher
            self._context_refresher = ContextRefresher(project.root)
            # Snapshot all source files currently relevant
            try:
                watched = list(project.root.rglob("*"))
                relevant = [p for p in watched
                            if p.is_file() and p.suffix in self._context_refresher.exts
                            and not project.should_skip(p)][:200]
                self._context_refresher.snapshot(relevant)
            except Exception:
                pass

    # ── Checkpoint helpers ────────────────────────────────────────────────────

    def _checkpoint_path(self, key: str) -> Path:
        # New layout: inside .multi_agent/sessions/<id>/<prefix>_<step>.md
        # when output_dir points to .multi_agent/sessions/
        from context import session_file
        if "sessions" in str(self.output_dir):
            return session_file(self.output_dir, self.session_id, key)
        # Legacy layout: outputs/<project>/<session>_<step>.md
        return self.output_dir / f"{self.session_id}_{key}.md"

    def _load_checkpoints(self):
        """Load any existing step outputs for this session."""
        loaded = []
        for key in self.STEP_KEYS:
            path = self._checkpoint_path(key)
            if path.exists():
                content = path.read_text(encoding="utf-8")
                # Strip the wrapper header added by _save()
                if "---" in content:
                    content = content.split("---", 1)[1].strip()
                self.results[key] = content
                loaded.append(key)
        if loaded:
            tprint(f"  📂 Loaded checkpoints: {', '.join(loaded)}")

    def _step_done(self, key: str) -> bool:
        return key in self.results

    @classmethod
    def list_sessions(cls, output_dir: str = "outputs", project_name: str | None = None) -> list[dict]:
        """Return resumable sessions. Searches within project subdir if project_name given, else all projects."""
        out = Path(output_dir)
        if not out.exists():
            return []

        # Determine which dirs to scan
        if project_name:
            scan_dirs = [out / project_name]
        else:
            scan_dirs = [d for d in out.iterdir() if d.is_dir()]
            if not scan_dirs:
                scan_dirs = [out]  # legacy flat layout fallback

        result = []
        for proj_dir in sorted(scan_dirs):
            sessions: dict[str, set] = {}
            for f in proj_dir.glob("*.md"):
                parts = f.stem.split("_", 2)
                if len(parts) < 3:
                    continue
                session_id = f"{parts[0]}_{parts[1]}"
                key = parts[2]
                if key in cls.STEP_KEYS:
                    sessions.setdefault(session_id, set()).add(key)

            for sid, done in sorted(sessions.items()):
                missing = [k for k in cls.STEP_KEYS if k not in done]
                if missing:
                    result.append({
                        "session_id": sid,
                        "project": proj_dir.name,
                        "completed": [k for k in cls.STEP_KEYS if k in done],
                        "missing": missing,
                    })
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_quota(self, upcoming_step: str) -> bool:
        """
        Check token usage before each step.
        - ≥80%: warn once, continue automatically
        - ≥95%: pause, show full report, ask user [C]ontinue / [S]top
        Returns False if user forse to stop.
        """
        t = self.tokens
        if t.should_pause():
            tprint(t.full_report())
            tprint(f"\n  🚨 QUOTA ALERT — {t.pct:.1f}% used, nearly out of budget!")
            tprint(f"  Next step: {upcoming_step}")
            tprint(f"  Estimated remaining: ~{t.remaining:,} tokens")
            while True:
                choice = input("  Continue? [C]ontinue / [S]top & save: ").strip().upper()
                if forice in ("C", "S", ""):
                    break
            if choice == "S":
                tprint("\n  💾 Pipeline stopped by user. Checkpoint saved — can resume later.")
                tprint(t.full_report())
                return False
        elif t.should_warn():
            t.mark_warned()
            tprint(f"\n  ⚠️  TOKEN WARNING — {t.pct:.1f}% of quota used  {t.short_status()}")
            tprint(f"  Remaining: ~{t.remaining:,} tokens — pipeline continuing automatically.\n")
        return True

    def _step_token_status(self, role: str):
        """Print brief token status after each step."""
        t = self.tokens
        tprint(f"  📊 {t.short_status()}  ({role} done)")

    def _header(self, step: int, total: int, role: str, status: str):
        tprint(f"\n{'═'*70}")
        tprint(f"  STEP {step}/{total} │ {role}")
        tprint(f"  ➜ {status}")
        tprint(f"{'═'*70}")

    def _dialogue_header(self, label: str):
        tprint(f"\n  {'─'*60}")
        tprint(f"  🔄 DIALOGUE PHASE: {label}")
        tprint(f"  {'─'*60}")

    def _save(self, key: str, content: str) -> str:
        path = self._checkpoint_path(key)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        wrapped = f"# {key.upper()} Output\n_Generated: {ts}_\n\n---\n\n{content}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wrapped, encoding="utf-8")
        tprint(f"  💾 Saved → {path.name}")

        # Optional: auto-commit code-producing step (dev) to the multi-agent branch
        if key == "dev" and self.git_helper and self.git_helper.is_repo():
            import os as _os
            if _os.environ.get("MULTI_AGENT_AUTO_COMMIT", "1") == "1":
                sha = self.git_helper.commit_step(
                    step="dev", session_id=self.session_id,
                    description="Dev implementation from multi-agent pipeline",
                )
                if sha:
                    tprint(f"  🌿 Auto-committed Dev changes → {sha[:8]}")
        return content

    def _skip(self, key: str, role: str):
        tprint(f"\n  ⏭️  SKIP {role} — checkpoint found ({self.session_id}_{key}.md)")

    def _detect_skill_for(self, agent, task_text: str, tasks: list | None = None):
        """Wrapper to auto-pick skill before running an agent. Safe-fallback on error.

        When `tasks` is provided, builds a compact metadata summary and
        forwards it so LLM-auto skill pick can route on semantic signals.
        """
        try:
            if hasattr(agent, "detect_skill"):
                meta = self._build_skill_metadata_summary(tasks) if tasks else None
                agent.detect_skill(task_text, task_metadata=meta)
        except Exception as e:
            tprint(f"  ⚠️  skill detect failed for {agent.ROLE}: {e}")

    def _build_skill_metadata_summary(self, tasks: list | None) -> dict:
        """
        Derive a compact {scopes, max_risk, max_complexity, impact_area,
        integrity_blacklist_hits, emergency_audit, hotfix_p0} signal bundle
        used to steer the LLM skill picker.

        Safe to call with empty tasks — returns an empty dict.
        """
        if not tasks:
            return {}
        scopes: set[str] = set()
        risks: list[str] = []
        complexities: list[str] = []
        impact_areas: set[str] = set()
        hot_p0 = False
        for t in tasks:
            try:
                m = t.get_metadata() if hasattr(t, "get_metadata") else None
            except Exception:
                m = None
            if m is None:
                continue
            scopes.add(m.context.scope)
            risks.append(m.context.risk_level)
            complexities.append(m.context.complexity)
            impact_areas.update(m.technical_debt.impact_area or [])
            if m.is_hot_p0():
                hot_p0 = True

        risk_rank = {"low": 0, "med": 1, "high": 2}
        cplx_rank = {"S": 0, "M": 1, "L": 2, "XL": 3}
        max_risk = max(risks, key=lambda r: risk_rank.get(r, 0)) if risks else None
        max_cpx  = max(complexities,
                        key=lambda c: cplx_rank.get(c, 0)) if complexities else None

        integrity_alerts: list[str] = []
        integrity = getattr(self, "_integrity", None)
        if integrity is not None:
            for area in impact_areas:
                try:
                    if integrity.module_forces_critic(area):
                        integrity_alerts.append(area)
                except Exception:
                    pass

        return {
            "scopes":                   sorted(scopes),
            "max_risk":                 max_risk,
            "max_complexity":           max_cpx,
            "impact_area":              sorted(impact_areas),
            "integrity_blacklist_hits": integrity_alerts,
            "emergency_audit":          bool(getattr(self, "_emergency_audit", False)),
            "hotfix_p0":                hot_p0,
        }

    def _maybe_refresh_context(self):
        """Long-session safeguard — rebuild scoped context if files changed externally."""
        refresher = getattr(self, "_context_refresher", None)
        if not refresher or not self.maintain_mode or not self.project_info:
            return
        try:
            need, changed = refresher.need_refresh()
            if not need:
                return
            tprint(f"\n  🔄 Context stale — {len(changed)} file(s) modified during session:")
            for p in changed[:5]:
                try:
                    rel = p.relative_to(self.project_info.root)
                    tprint(f"     • {rel}")
                except ValueError:
                    tprint(f"     • {p}")
            # Rebuild scoped context from latest disk state
            from context import build_scoped_context
            task_hint = self.results.get("ba", "")[:500] or ""
            new_ctx = build_scoped_context(self.project_info, task_hint)
            for key in ("ba", "techlead", "dev", "test"):
                if key in self.agents:
                    self.agents[key].project_context = new_ctx
            self.investigator.project_context = new_ctx
            tprint(f"  ✅ Context refreshed ({len(new_ctx):,} chars)")
        except Exception as e:
            tprint(f"  ⚠️  Context refresh failed: {e}")

    def _apply_dynamic_weights(self, review: dict, agent):
        """Recompute review score using scope-aware weights from the active skill."""
        try:
            from learning.score_adjuster import ScoreAdjuster
        except ImportError:
            return review
        active = getattr(agent, "_active_skill", None)
        scope  = (active or {}).get("detected_scope")
        if not scope:
            return review
        adj = ScoreAdjuster()
        new_review = adj.recompute_with_scope(review, scope)
        if new_review.get("score_before_scope_reweight") is not None:
            old = new_review["score_before_scope_reweight"]
            new_final = new_review["score"]
            if old != new_final:
                tprint(f"  ⚖️  [{agent.ROLE}] score re-weighted by scope={scope}: {old} → {new_final}/10")
        return new_review

    # Patterns that flag a TechLead output as touching "core architecture".
    # When any of these is matched, we DO run Critic for TL even though
    # `techlead` is not in CRITIC_STEPS — architecture mistakes are expensive
    # to find downstream.
    _CORE_FILE_PATTERNS = (
        r"\bmain\.dart\b",
        r"\bapp_router(?:\.dart|\.ts|\.tsx)?\b",
        r"\brouter\.dart\b|\broutes\.dart\b",
        r"\binjection(?:_container)?\.dart\b",
        r"\bdependency[_-]injection\b",
        r"\bservice_locator\.dart\b",
        r"\b(?:Base|Abstract)\w*(?:Repository|UseCase|Bloc|Cubit|Widget|Screen)\b",
        r"\bapp\.dart\b|\bapp\.tsx?\b",
        r"\bdatabase_helper\b",
        r"\bnetwork_client\b|\bdio_client\b",
        r"\bauth_interceptor\b",
        r"\b_links:\s*\[[^\]]*(main\.dart|app_router|service_locator)",
        r"\bcore/(?:router|di|network|storage|base|config)\b",
    )

    def _techlead_touches_core(self, tl_output: str) -> tuple[bool, list[str]]:
        """Return (touches, matched_patterns). Used to gate Critic for TL."""
        import re
        if not tl_output:
            return False, []
        matched: list[str] = []
        for pat in self._CORE_FILE_PATTERNS:
            m = re.search(pat, tl_output, re.IGNORECASE)
            if m:
                matched.append(m.group(0))
        return (len(matched) > 0, matched)

    # Map internal step key → role name used in TaskMetadata.flow_control.skip_critic.
    _KEY_TO_ROLE = {
        "pm":       "PM",
        "ba":       "BA",
        "design":   "Design",
        "techlead": "TechLead",
        "dev":      "Dev",
        "test":     "QA",
    }

    def _get_audit_log(self):
        """Lazily construct the session-level AuditLog."""
        if self._audit_log is None:
            from learning.audit_log import AuditLog
            from agents.base_agent import _RULES_DIR
            session_dir = self._checkpoint_path("ba").parent
            profile_dir = _RULES_DIR / self.profile
            self._audit_log = AuditLog(session_dir, profile_dir)
        return self._audit_log

    def _record_critic_skip(self, key: str, tasks: list) -> None:
        """Remember which roles we skipped for each task — needed for RCA later."""
        role = self._KEY_TO_ROLE.get(key, key.upper())
        for t in tasks:
            tid = getattr(t, "id", None)
            if not tid:
                continue
            self._skipped_critic_by_task.setdefault(tid, []).append(role)
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
            if m and tid not in self._skip_snapshot:
                self._skip_snapshot[tid] = m.to_dict()

    def _trigger_emergency_audit(self, blockers: list[str], tasks: list,
                                  agent_in_charge: str = "Dev") -> list[dict]:
        """Activate Emergency Audit Mode: record false-negatives + force Critic.

        Called the first time QA surfaces BLOCKERs. Returns list of audit
        entries written so callers can render a human-friendly summary.
        """
        if self._emergency_audit:
            # Already active — still log, but don't re-announce.
            pass
        else:
            tprint(f"\n  {'🚨'*3}  EMERGENCY AUDIT MODE  {'🚨'*3}")
            tprint(f"     QA found {len(blockers)} BLOCKER(s) on tasks that had "
                   f"Critic skipped — activating full audit.")
            tprint(f"     Critic will be FORCED ON for the remainder of this session.")
            tprint(f"  {'─'*60}")
            self._emergency_audit = True

        # Only log false-negatives for tasks where we actually skipped some role.
        entries: list[dict] = []
        if not tasks:
            return entries
        from learning.audit_log import classify_outcome, make_root_cause_hint
        outcome = classify_outcome(blockers)
        audit = self._get_audit_log()
        for t in tasks:
            tid = getattr(t, "id", None)
            if not tid:
                continue
            skipped_roles = self._skipped_critic_by_task.get(tid, [])
            if not skipped_roles:
                continue   # no skip happened → not a false-negative
            meta = self._skip_snapshot.get(tid, {})
            hint = make_root_cause_hint(meta, skipped_roles, blockers)
            e = audit.record(
                session_id=self.session_id,
                task_id=tid,
                predicted_metadata=meta,
                skipped_for_roles=skipped_roles,
                actual_outcome=outcome,
                blockers=blockers,
                agent_in_charge=agent_in_charge,
                root_cause_hint=hint,
            )
            entries.append(e)
            tprint(f"     📼 RCA logged for {tid}: {hint}")

            # Mutate metadata in memory so downstream (revise loop, RuleOptimizer)
            # sees the upgraded risk.
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
            if m is not None:
                m.context.risk_level = "high"
                # Clear skip_critic list so future runs respect the upgrade.
                m.flow_control.skip_critic = []

            # Feed the failure into IntegrityRules so future sessions learn.
            if getattr(self, "_integrity", None) is not None:
                change = self._integrity.record_failure(
                    module=getattr(t, "module", ""),
                    impact_areas=(m.technical_debt.impact_area if m else []),
                    agent_in_charge=agent_in_charge,
                    skipped_roles=skipped_roles,
                    blockers=blockers,
                )
                bumped = ", ".join(
                    f"{b['module']}({b['count']})" for b in change.get("modules_bumped", [])
                )
                if bumped:
                    tprint(f"     🏷  Module counter bumped: {bumped}")
                for fw in change.get("new_forced_windows", []):
                    tprint(f"     🔒 {fw['role']} entered forced-Critic "
                           f"window ({fw['window']} tasks)")
                for kw in change.get("keywords_promoted", []):
                    tprint(f"     🆙 Keyword risk: '{kw['keyword']}' → "
                           f"{kw['risk']}")

        # Regenerate the human-readable integrity.md artefact (the demo file).
        if entries and getattr(self, "_integrity", None) is not None:
            from agents.base_agent import _RULES_DIR
            path = self._integrity.write_integrity_rules_md(_RULES_DIR / self.profile)
            tprint(f"     📜 integrity.md updated → {path}")

        return entries

    # ── PM is the authority on `scope` ───────────────────────────────────────

    # PM.kind → canonical TaskMetadata.context.scope value.
    _PM_KIND_TO_SCOPE = {
        "feature":       "feature",
        "bug_fix":       "bug_fix",
        "ui_tweak":      "ui_tweak",
        "refactor":      "refactor",
        "hotfix":        "hotfix",
        "investigation": "investigation",
    }

    def _apply_pm_metadata(self, route, tasks: list) -> int:
        """
        PM is the **single source of truth** for `scope`. After BA parses
        tasks, we overwrite `metadata.context.scope` to match PM's kind so
        downstream gates, audit log and analytics always agree with PM.

        If PM decomposed the request into sub_tasks, we match each sub_task
        description against task titles (case-insensitive substring) and
        apply that sub_task's kind specifically; tasks with no match inherit
        the top-level kind.

        Returns: number of tasks whose metadata was overwritten.
        """
        if route is None or not tasks:
            return 0
        default_scope = self._PM_KIND_TO_SCOPE.get(route.kind, route.kind)
        sub_tasks = list(getattr(route, "sub_tasks", []) or [])

        def _match_sub(task) -> str | None:
            """Try to map this task to one of PM's sub_tasks via title match."""
            if not sub_tasks:
                return None
            hay = (task.title or "").lower() + " " + (task.description or "").lower()
            for st in sub_tasks:
                desc = (st.get("desc") or "").lower()
                if desc and desc[:30] in hay:
                    return self._PM_KIND_TO_SCOPE.get(st.get("kind"), st.get("kind"))
            return None

        changed = 0
        for t in tasks:
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
            if m is None:
                continue
            chosen = _match_sub(t) or default_scope
            if m.context.scope != chosen:
                m.context.scope = chosen
                changed += 1
        return changed

    def _fast_track_announce(self, key: str, context: dict) -> None:
        """Print a human-readable 'Fast-Track' message explaining why Critic
        was skipped for this step. Uses metadata in context when available."""
        tasks = context.get("tasks") or []
        reason = "low-risk step"
        if tasks:
            if any(t.get_metadata().is_hot_p0() for t in tasks):
                reason = "hotfix P0 — skipping intermediate Critic for speed"
            elif all(t.get_metadata().is_low_risk_small() for t in tasks):
                reason = "all tasks Low-risk + S complexity → Fast-Track mode"
            elif key == "techlead":
                reason = "standard op (no core-file touches, no L/XL) → straight to Dev"

        # Big marquee message for the "Fast-Track" narrative.
        tprint(f"  🚀 Fast-Track: Critic skipped for [{key}] ({reason}) "
               f"— saving ~1 LLM call")

    def _critic_enabled_for(self, key: str, output: str = "",
                             context: dict | None = None) -> bool:
        """True iff Critic should run for this step key.

        Decision order (highest precedence first):
          1. MULTI_AGENT_CRITIC_ALL=1 → always run (legacy).
          2. Role-specific env override (MULTI_AGENT_TL_CRITIC_ALWAYS/NEVER).
          3. Metadata-driven rules derived from the tasks in `context["tasks"]`:
             3a. If ANY task is `hotfix+P0` → skip PM/BA/TL, keep Dev/QA.
             3b. If ANY task.touches_core() → force Critic (payment/auth/core).
             3c. If ALL tasks are S+low → skip PM/BA/TL.
             3d. Per-task `flow_control.skip_critic` inclusion of this role.
          4. TechLead secondary rule (unchanged): any L/XL or bug/hotfix type.
          5. Fallback: key in CRITIC_STEPS (dev, test).

        `context` may contain:
          {"tasks": [Task, Task, ...]}   # preferred (metadata-driven)
          {"tl_complexities": ["S","M"], "tl_types": ["logic","bug"]}  # legacy
        """
        if os.environ.get("MULTI_AGENT_CRITIC_ALL", "0") == "1":
            return True
        # Emergency Audit Mode — a false-negative has already bitten us in
        # this session, so the whole pipeline is demoted to "Critic everywhere"
        # until the session ends.
        if getattr(self, "_emergency_audit", False):
            return True

        # Integrity rules — learned from past sessions.
        integrity = getattr(self, "_integrity", None)
        role = self._KEY_TO_ROLE.get(key, key.upper())

        # (a) Forced-Critic window for a role whose reputation is bad.
        if integrity is not None and integrity.role_has_forced_window(role):
            tprint(f"  🛡  Forced-Critic window active for {role} — running Critic")
            integrity.consume_forced_window(role)
            return True

        # (b) Module blacklist: any task's impact_area matches → force Critic.
        if integrity is not None:
            tasks_ctx = (context or {}).get("tasks") or []
            for t in tasks_ctx:
                m = t.get_metadata() if hasattr(t, "get_metadata") else None
                if not m:
                    continue
                areas = list(m.technical_debt.impact_area) + [
                    getattr(t, "module", "") or ""
                ]
                if any(integrity.module_forces_critic(a) for a in areas if a):
                    tprint(f"  🛡  Module in integrity blacklist → running Critic")
                    return True

        # TechLead env overrides kept for backward compat.
        if key == "techlead":
            if os.environ.get("MULTI_AGENT_TL_CRITIC_NEVER", "0") == "1":
                return False
            if os.environ.get("MULTI_AGENT_TL_CRITIC_ALWAYS", "0") == "1":
                return True

        role = self._KEY_TO_ROLE.get(key, key.upper())
        ctx = context or {}
        tasks = ctx.get("tasks") or []

        # Backward-compat: if caller only passed tl_complexities/tl_types but
        # no full Task list, synthesise a lightweight context.
        complexities: list[str] = [str(c).upper() for c in ctx.get("tl_complexities", [])]
        types:        list[str] = [str(t).lower() for t in ctx.get("tl_types", [])]
        for t in tasks:
            m = t.get_metadata() if hasattr(t, "get_metadata") else None
            if m:
                complexities.append(m.context.complexity)
                types.append(m.context.scope)

        # ── Metadata-driven rules ──
        if tasks:
            any_hot_p0       = any(t.get_metadata().is_hot_p0()       for t in tasks)
            any_touches_core = any(t.get_metadata().touches_core()    for t in tasks)
            all_low_small    = all(t.get_metadata().is_low_risk_small() for t in tasks)

            # 3a. Hotfix P0 → skip PM/BA/TL, keep Dev+QA regardless.
            if any_hot_p0 and key in ("pm", "ba", "techlead", "design", "test_plan"):
                return False

            # 3b. Core-touch task → force Critic on TL (and Dev/Test are already on).
            if any_touches_core and key == "techlead":
                return True

            # 3c. All tasks low-risk + S complexity → skip PM/BA/TL bundle.
            if all_low_small and key in ("pm", "ba", "techlead"):
                return False

            # 3d. Respect per-task skip list: if every task in scope has skipped
            # this role, honour it.
            if all(role in t.get_metadata().flow_control.skip_critic for t in tasks):
                return False

        # ── TechLead secondary rules (unchanged) ──
        if key == "techlead":
            if any(c in ("L", "XL") for c in complexities):
                return True
            if any(t in ("bug", "bug_fix", "hotfix") for t in types):
                return True
            if complexities and all(c in ("S", "M") for c in complexities):
                return False
            touches, _ = self._techlead_touches_core(output)
            return touches

        return key in self.CRITIC_STEPS

    def _review_only(self, key: str, agent, output: str,
                      original_prompt: str = "", context: dict | None = None) -> str:
        """Critic-review an already-produced output (no produce_fn).

        Used for agents like TechLead whose output is assembled from multiple
        internal calls — we don't want to re-run production, only ask Critic
        to score the final artefact.
        """
        if not self._critic_enabled_for(key, output, context):
            self._fast_track_announce(key, context or {})
            self._record_critic_skip(key, (context or {}).get("tasks") or [])
            return output
        if key == "techlead":
            _, matches = self._techlead_touches_core(output)
            tprint(f"  🏛️  TechLead touched core files {matches[:3]} → running Critic")

        for round_num in range(1, self.critic.MAX_ROUNDS + 1):
            tprint(f"  🔍 Critic reviewing [{key}] round {round_num}...")
            review = self.critic.evaluate(
                agent.ROLE, output, agent_key=key,
                original_context=original_prompt,
            )
            review = self._apply_dynamic_weights(review, agent)
            threshold = review.get("pass_threshold", 7)
            review["verdict"] = "PASS" if review["score"] >= threshold else "REVISE"
            self.critic.print_review(agent.ROLE, review, round_num)
            self.critic_reviews.append({**review, "agent_key": key,
                                        "agent_role": agent.ROLE, "round": round_num})
            if review["verdict"] == "PASS":
                break
            if round_num < self.critic.MAX_ROUNDS:
                tprint(f"\n  🔄 {agent.ROLE} is improving its output...")
                output = agent.revise(output, review["revision_guide"], original_prompt)
                self._save(key, output)
                self.results[key] = output
            else:
                output = self._escalate(key, agent, output, review, original_prompt)
        return output

    def _run_with_review(self, key: str, agent, produce_fn, original_prompt: str = "") -> str:
        """Run agent, then Critic reviews with multi-dimensional scoring. Max 2 rounds.

        If `key` is not in CRITIC_STEPS (and MULTI_AGENT_CRITIC_ALL is off), skip
        the Critic + revise loop entirely — save ~1 Critic call + up to 1 revise
        call per low-risk step.
        """
        self._maybe_refresh_context()
        self._detect_skill_for(agent, original_prompt)
        output = produce_fn()
        self._save(key, output)
        self.results[key] = output

        if not self._critic_enabled_for(key, output):
            self._fast_track_announce(key, {})
            # _run_with_review doesn't currently pass a task list, so we can
            # only log the skip with an empty task scope. Emergency audit
            # will still fire via the TechLead path which does pass tasks.
            return output
        if key == "techlead":
            _, matches = self._techlead_touches_core(output)
            tprint(f"  🏛️  TechLead touched core files {matches[:3]}"
                   f" → running Critic")

        for round_num in range(1, self.critic.MAX_ROUNDS + 1):
            tprint(f"  🔍 Critic reviewing [{key}] round {round_num}...")
            review = self.critic.evaluate(
                agent.ROLE, output,
                agent_key=key,
                original_context=original_prompt,
            )
            # Scope-aware re-weighting BEFORE verdict check
            review = self._apply_dynamic_weights(review, agent)
            # Verdict recompute based on adjusted score
            threshold = review.get("pass_threshold", 7)
            review["verdict"] = "PASS" if review["score"] >= threshold else "REVISE"
            self.critic.print_review(agent.ROLE, review, round_num)
            self.critic_reviews.append({**review, "agent_key": key, "agent_role": agent.ROLE, "round": round_num})
            if review["verdict"] == "PASS":
                break
            if round_num < self.critic.MAX_ROUNDS:
                tprint(f"\n  🔄 {agent.ROLE} is improving its output...")
                output = agent.revise(output, review["revision_guide"], original_prompt)
                self._save(key, output)
                self.results[key] = output
            else:
                output = self._escalate(key, agent, output, review, original_prompt)
        return output

    def _escalate(self, key: str, agent, output: str, review: dict, original_prompt: str) -> str:
        """
        Called when agent still REVISE after MAX_ROUNDS.
        Ask user: [C]ontinue with current / [R]etry one more round / [S]kip step.
        """
        tprint(f"\n  {'═'*60}")
        tprint(f"  🚨 ESCALATION — {agent.ROLE} [{key}] score {review['score']}/10 after {self.critic.MAX_ROUNDS} rounds")
        tprint(f"  {'═'*60}")
        if review["weaknesses"]:
            tprint("  Remaining issues:")
            for w in review["weaknesses"][:3]:
                tprint(f"    • {w}")
        tprint(f"\n  Options:")
        tprint(f"    [C] Continue with current output (score {review['score']}/10)")
        tprint(f"    [R] Retry one more round")
        tprint(f"    [S] Skip this step (warning: downstream agents will lack input)")

        while True:
            choice = input("  Choose [C/R/S]: ").strip().upper()
            if forice in ("C", "R", "S", ""):
                break
            tprint("  Enter C, R, or S.")

        if choice == "R":
            tprint(f"\n  🔄 Retrying one more round per user request...")
            output = agent.revise(output, review["revision_guide"], original_prompt)
            self._save(key, output)
            self.results[key] = output
            extra_review = self.critic.evaluate(agent.ROLE, output, agent_key=key, original_context=original_prompt)
            self.critic.print_review(agent.ROLE, extra_review, self.critic.MAX_ROUNDS + 1)
            self.critic_reviews.append({**extra_review, "agent_key": key, "agent_role": agent.ROLE, "round": self.critic.MAX_ROUNDS + 1})
            tprint(f"  {'─'*60}")
        elif choice == "S":
            tprint(f"\n  ⏭️  Skipping {agent.ROLE} — downstream agents will have no input from this step.")
            output = f"[SKIPPED by user — score {review['score']}/10 after {self.critic.MAX_ROUNDS} rounds]"
            self.results[key] = output
        else:
            tprint(f"\n  ▶  Continuing with current output (score {review['score']}/10).")

        return output

    def _save_conversations(self):
        """Save full agent-to-agent conversation log."""
        if not self.bus.log:
            return
        lines = [f"# Agent Conversations – {self.session_id}\n\n"]
        for i, msg in enumerate(self.bus.log, 1):
            lines.append(f"## [{i}] {msg.from_agent} → {msg.to_agent}  _{msg.timestamp}_\n\n")
            lines.append(f"**Question:**\n{msg.content}\n\n")
            if msg.response:
                lines.append(f"**Answer:**\n{msg.response}\n\n")
            lines.append("---\n\n")
        path = self.output_dir / f"{self.session_id}_conversations.md"
        path.write_text("".join(lines), encoding="utf-8")
        tprint(f"  💬 Conversations saved → {path.name}")

    # ── Main pipeline ─────────────────────────────────────────────────────────

    # ── PM router (Step 0) ────────────────────────────────────────────────────

    def _run_pm_router(self, product_idea: str) -> RouteDecision:
        """
        Classify the request and decide which sub-pipeline to run.

        Returns a RouteDecision. The caller is expected to consult
        decision.dispatch_steps() to know which agents to run.

        Honors checkpoints — if pm.md already exists for this session, parse it
        back into a RouteDecision and skip the LLM call.
        """
        pm: PMAgent = self.agents["pm"]

        # Resume from checkpoint if present.
        if self._step_done("pm"):
            cached = self.results.get("pm", "")
            parsed_kind = None
            parsed_conf = 0.85  # assume previous run had good confidence
            import re as _re
            m = _re.search(r"\*\*Kind\*\*:\s*`([a-z_]+)`", cached)
            if m and m.group(1) in PM_ALL_KINDS:
                parsed_kind = m.group(1)
            if parsed_kind:
                self._skip("pm", "PM Router")
                return RouteDecision(
                    kind=parsed_kind,
                    confidence=parsed_conf,
                    reason="(restored from checkpoint)",
                    source="checkpoint",
                )
            # Fall through to re-run if checkpoint was malformed.

        if not self._check_quota("PM routing"):
            # Quota blown — default to feature to preserve legacy behavior.
            return RouteDecision(
                kind="feature", confidence=0.5,
                reason="PM skipped due to token quota.", source="default",
            )

        self._header(0, len(self.PIPELINE), "PM Router", "classifying request...")
        pm._current_step = "pm"
        try:
            pm.detect_skill(product_idea)
        except Exception:
            pass
        decision = pm.classify(product_idea)

        # Low-confidence path → ask user to confirm the kind.
        if decision.confidence < 0.6:
            decision = self._pm_clarify_with_user(decision, product_idea)

        tprint(f"\n  🧭 PM routed → kind=`{decision.kind}` "
               f"(confidence={decision.confidence:.2f}, via {decision.source})")
        tprint(f"     Reason: {decision.reason[:120]}")
        tprint(f"     Dispatch: {' → '.join(decision.dispatch_steps())}")

        self._save("pm", decision.to_markdown())
        self.results["pm"] = decision.to_markdown()
        self._step_token_status("PM")
        return decision

    def _pm_clarify_with_user(self, decision: RouteDecision, product_idea: str) -> RouteDecision:
        """Interactive fallback when PM confidence < 0.6."""
        tprint(f"\n  ⚠️  PM confidence {decision.confidence:.2f} is low — please confirm.")
        tprint(f"     PM suggestion: {decision.kind}")
        tprint(f"     Reason: {decision.reason}")
        tprint(f"\n     Kinds:")
        for i, k in enumerate(PM_ALL_KINDS, 1):
            marker = " ← PM pick" if k == decision.kind else ""
            tprint(f"       {i}. {k}{marker}")

        while True:
            raw = input("\n     Pick kind [1-5] or Enter to accept PM pick: ").strip()
            if not raw:
                return decision
            if raw.isdigit() and 1 <= int(raw) <= len(PM_ALL_KINDS):
                chosen = PM_ALL_KINDS[int(raw) - 1]
                return RouteDecision(
                    kind=chosen,
                    confidence=1.0,
                    reason=f"User confirmed after low-confidence PM pick ({decision.kind}).",
                    source="user",
                )
            if raw in PM_ALL_KINDS:
                return RouteDecision(
                    kind=raw, confidence=1.0,
                    reason="User confirmed kind by name.",
                    source="user",
                )
            tprint("     Invalid choice. Enter 1-5 or a kind name.")

    def _run_investigation_path(self, product_idea: str) -> dict:
        """Sub-pipeline for kind=investigation — skip BA/Design/TL/Dev/Test."""
        self._header(1, 1, "Code Investigator", "answering request via investigation only...")
        if not self._check_quota("Investigation"):
            return {}
        try:
            if not self.investigator.project_context:
                # Investigation without a project context still runs, but degrades to Q&A.
                tprint("  ℹ️  No project context loaded — running Q&A mode.")
            report = self.investigator.investigate(product_idea)
        except Exception as e:
            tprint(f"  ❌ Investigation failed: {e}")
            return {}

        if report:
            self.investigator.print_report(report)
            self._save("investigation", report)
            self.results["investigation"] = report
        self._step_token_status("Investigation")
        return self.results

    # ── Task-based pipeline (NEW FLOW) ────────────────────────────────────────

    def _run_task_based_pipeline(self, product_idea: str,
                                  resources: dict | None = None,
                                  allowed_steps: list[str] | None = None,
                                  pm_route: "RouteDecision | None" = None) -> dict:
        """
        New flow:
          1. BA.produce_tasks → classified task list (ui/logic/bug/hotfix/mixed)
          2. Split: UI tasks → Design.process_ui_tasks (find or create)
             → BA.consolidate_tasks (merge design refs)
          3. TechLead.prioritize_and_assign → SprintPlan with adjustments
          4. Parallel:
               Dev.implement per sprint in priority order
               Test.plan_from_sprint
          5. QA review → Dev fix loop per sprint
        """
        from learning.task_models import parse_tasks, split_by_type, TaskType

        ba   = self.agents["ba"]
        des  = self.agents["design"]
        tl   = self.agents["techlead"]
        dev  = self.agents["dev"]
        qa   = self.agents["test"]

        def _allowed(step: str) -> bool:
            """True if step should run. None = legacy 'run everything' mode."""
            return allowed_steps is None or step in allowed_steps

        if allowed_steps is not None:
            tprint(f"\n  🧭 Sub-pipeline from PM: {' → '.join(allowed_steps)}")

        # ── STEP 1: BA builds classified task list ────────────────────────────
        if not _allowed("ba") and not self._step_done("ba"):
            tprint("  ⏭️  BA skipped (not in PM dispatch plan)")
            tasks_md = ""
        elif self._step_done("ba"):
            self._skip("ba", "BA (task producer)")
            tasks_md = self.results["ba"]
        else:
            if not self._check_quota("BA task production"): return {}
            self._header(1, 6, "BA (task producer)", "classify tasks...")
            ba._current_step = "ba"
            tasks_md = self._run_with_review(
                "ba", ba,
                lambda: ba.produce_tasks(product_idea),
                original_prompt=product_idea,
            )
            self._step_token_status("BA")

        tasks = parse_tasks(tasks_md) if tasks_md else []
        if not tasks:
            if allowed_steps is not None and "ba" not in allowed_steps:
                # BA was intentionally skipped — synthesize a placeholder task
                # so downstream Dev/Test still have something to chew on.
                from learning.task_models import Task, TaskType, Priority, Complexity, Risk, BusinessValue
                tasks = [Task(
                    id="TASK-PM-001",
                    title=(product_idea.strip().splitlines() or ["Request from PM"])[0][:80],
                    description=product_idea,
                    type=TaskType.LOGIC,
                    priority=Priority.P2,
                    complexity=Complexity.M,
                    risk=Risk.MED,
                    business_value=BusinessValue.NORMAL,
                )]
                tasks_md = tasks[0].to_markdown()
            else:
                tprint("\n  ❌ Could not parse any tasks from BA output — STOP.")
                tprint("     Check that BA output follows format `## TASK-XXX | type=... | priority=...` no.")
                return {}

        # Auto-split MIXED tasks into UI + Logic children
        from learning.task_models import expand_mixed_tasks
        mixed_count = sum(1 for t in tasks if t.type.value == "mixed")
        if mixed_count:
            tasks, _links = expand_mixed_tasks(tasks)
            tprint(f"\n  ✂️  Split {mixed_count} mixed tasks → {mixed_count*2} children "
                   f"(UI blocking Logic)")

        # PM is authoritative for `scope` — override any BA-written value so
        # downstream gates / audit log / RuleOptimizer all agree with PM.
        pm_stamped = self._apply_pm_metadata(pm_route, tasks)
        if pm_stamped:
            tprint(f"  🧭 PM stamped scope on {pm_stamped}/{len(tasks)} task(s) "
                   f"(kind=`{pm_route.kind}`)")

        tprint(f"\n  📋 Parsed {len(tasks)} tasks from BA:")
        split = split_by_type(tasks)
        tprint(f"     UI: {len(split['ui'])}  Logic: {len(split['logic'])}  "
               f"Bug: {len(split['bug'])}  Hotfix: {len(split['hotfix'])}")

        # ── STEP 2: Design handles UI tasks (find or create) ─────────────────
        design_refs: dict[str, str] = {}
        ui_tasks = split["ui"]
        if not _allowed("design") and not self._step_done("design"):
            tprint("  ⏭️  Design skipped (not in PM dispatch plan)")
        elif ui_tasks and not self._step_done("design"):
            if not self._check_quota("Design UI tasks"): return {}
            self._header(2, 6, "Designer", f"processing {len(ui_tasks)} UI tasks (reuse or create)...")
            des._current_step = "design"
            des.detect_skill(product_idea,
                              task_metadata=self._build_skill_metadata_summary(ui_tasks))
            existing_ds = self.project_info and self._find_existing_design_system() or ""
            design_refs = des.process_ui_tasks(ui_tasks, existing_ds)
            reused = sum(1 for r in design_refs.values() if r.startswith("[REUSE]"))
            created = len(design_refs) - reused
            tprint(f"  ✅ Design: {reused} reused, {created} newly created")

            # Save design output
            design_md = "# Design Refs per UI Task\n\n" + "\n\n".join(
                f"## {tid}\n{ref}" for tid, ref in design_refs.items()
            )
            self._save("design", design_md)
            self.results["design"] = design_md
            self._step_token_status("Design")
        elif self._step_done("design"):
            self._skip("design", "Designer")

        # ── STEP 3: BA consolidates tasks with design refs ─────────────────────
        if design_refs:
            tprint(f"\n  🔄 BA consolidate {len(design_refs)} design refs into task list")
            tasks_md = ba.consolidate_tasks(tasks_md, design_refs)
            self._save("ba", tasks_md)
            self.results["ba"] = tasks_md
            # Re-parse to pick up design_ref fields
            tasks = parse_tasks(tasks_md)

        # ── STEP 4: TechLead prioritize + assign sprint ──────────────────────
        if not _allowed("techlead") and not self._step_done("techlead"):
            tprint("  ⏭️  TechLead skipped (not in PM dispatch plan) — using tasks directly")
            sprint_md = tasks_md  # Dev will consume the raw task list instead.
            sprint_plan = None
        elif self._step_done("techlead"):
            self._skip("techlead", "Tech Lead (prioritizer)")
            sprint_md = self.results["techlead"]
            sprint_plan = None
        else:
            if not self._check_quota("TechLead prioritize"): return {}
            self._header(3, 6, "Tech Lead (prioritizer)",
                         "evaluating resources + prioritize sprint...")
            tl._current_step = "techlead"
            tl.detect_skill(product_idea,
                             task_metadata=self._build_skill_metadata_summary(tasks))
            # Role contribution: TL enriches metadata (impact_area + risk bump)
            # before its Critic gate is evaluated.
            if hasattr(tl, "enrich_metadata"):
                changed = tl.enrich_metadata(tasks)
                if changed:
                    tprint(f"  🧠 TechLead enriched metadata on {changed} task(s)")

            # Option B — proactive spec review. Only fires when the regex
            # smell test flags at least one task, so spec-clean sessions
            # pay zero tokens. If BA answers, task ACs are patched in place
            # so downstream Dev/Test see the clarified spec.
            if hasattr(tl, "review_ba_spec_batch"):
                try:
                    review = tl.review_ba_spec_batch(ba, tasks)
                    flagged = review.get("flagged") or []
                    if flagged:
                        tprint(f"  🗣  TL batch-reviewed BA spec: "
                               f"{len(flagged)} task(s) clarified via BA.")
                        # Update BA checkpoint so resume sees patched tasks.
                        self.results["ba"] = "\n\n".join(t.to_markdown() for t in tasks)
                        self._save("ba", self.results["ba"])
                except Exception as e:
                    tprint(f"  ⚠️  TL batch review failed: {e}")

            result = tl.prioritize_and_assign(tasks, resources)
            sprint_plan = result["sprint_plan"]
            sprint_md = result["summary_markdown"]
            self._save("techlead", sprint_md)
            self.results["techlead"] = sprint_md
            tprint(f"\n{sprint_plan.summary()}")
            if result["adjustments"]:
                tprint(f"\n  🔧 TechLead adjusted {len(result['adjustments'])} estimates")
            self._step_token_status("TechLead")

            # Conditional Critic: gate by metadata (complexity, risk, impact).
            tl_ctx = {
                "tasks": tasks,
                # Legacy fields — kept for older callers & fallback logic.
                "tl_complexities": [t.complexity.value for t in tasks],
                "tl_types":        [t.type.value       for t in tasks],
            }
            sprint_md = self._review_only(
                "techlead", tl, sprint_md,
                original_prompt=tasks_md, context=tl_ctx,
            )
            self.results["techlead"] = sprint_md

        # ── STEP 5: parallel Test Plan + Dev ─────────────────────────────────
        if not _allowed("test_plan") and not self._step_done("test_plan"):
            tprint("  ⏭️  Test plan skipped (not in PM dispatch plan)")
        elif not self._step_done("test_plan"):
            if not self._check_quota("Test plan from sprint"): return {}
            self._header(4, 6, "QA (planner)", "writing test plan in sprint priority order...")
            qa._current_step = "test_plan"
            qa.detect_skill(product_idea,
                             task_metadata=self._build_skill_metadata_summary(tasks))
            if sprint_plan is not None:
                test_plan = qa.plan_from_sprint(sprint_plan, tasks)
            else:
                # TechLead was skipped — plan from raw tasks.
                test_plan = qa.plan_from_sprint(None, tasks) if hasattr(qa, "plan_from_sprint") else ""
            self._save("test_plan", test_plan)
            self.results["test_plan"] = test_plan
            self._step_token_status("TestPlan")

        if not _allowed("dev") and not self._step_done("dev"):
            tprint("  ⏭️  Dev skipped (not in PM dispatch plan)")
        elif not self._step_done("dev"):
            if not self._check_quota("Dev implementation"): return {}
            self._header(5, 6, "Developer", "implementing tasks in sprint order...")
            dev._current_step = "dev"
            dev.detect_skill(product_idea,
                              task_metadata=self._build_skill_metadata_summary(tasks))
            # Feed top-priority tasks first
            top_tasks_md = "\n\n".join(
                t.to_markdown() for t in sorted(tasks, key=lambda x: -x.priority_score)[:8]
            )
            impl = self._run_with_review(
                "dev", dev,
                lambda: dev.implement_with_clarification(
                    sprint_md, self.results.get("design", ""),
                    top_tasks_md, tl_clarification="", tl_task_assignment=sprint_md,
                ),
                original_prompt=top_tasks_md,
            )
            self._step_token_status("Dev")
            self._save_implementation_files(impl)

        # ── STEP 6: QA review + fix loop ─────────────────────────────────────
        if not _allowed("test") and not self._step_done("test"):
            tprint("  ⏭️  QA review skipped (not in PM dispatch plan)")
        elif not self._step_done("test"):
            if not self._check_quota("QA review"): return {}
            self._header(6, 6, "QA (reviewer)", "verifying implementation against test plan...")
            qa._current_step = "test_review"
            review = self._run_with_review(
                "test", qa,
                lambda: qa.review_implementation(
                    self.results.get("test_plan", ""),
                    self.results.get("dev", ""),
                    "",
                ),
                original_prompt=self.results.get("test_plan", ""),
            )
            implementation = self._qa_dev_loop(
                qa, dev, tl, self.results.get("test_plan", ""),
                self.results.get("dev", ""), "", review, product_idea,
                tasks=tasks,
            )
            self._save_flutter_tests(self.results.get("test", ""))

        # ── Wrap up ──────────────────────────────────────────────────────────
        self._save_conversations()
        self._save_summary()
        self._collect_auto_feedback()
        self._write_html_report()
        self.bus.print_log()
        tprint(f"\n{'✅'*5}  TASK-BASED PIPELINE COMPLETE  {'✅'*5}")
        tprint(self.tokens.full_report())
        return self.results

    def _find_existing_design_system(self) -> str:
        """Scan project context for design system / theme / token files."""
        from context import build_scoped_context
        if not self.project_info:
            return ""
        try:
            return build_scoped_context(
                self.project_info,
                task="design system tokens colors typography theme",
                max_total_chars=8000,
            )
        except Exception:
            return ""
    def run(self, product_idea: str, resources: dict | None = None) -> dict:
        """
        resources:
          {"dev_slots": 2, "sprint_hours": 80, "sprints_ahead": 3}
        """
        banner = "🚀" * 5
        tprint(f"\n{banner}  PIPELINE  {banner}")
        tprint(f"  Session : {self.session_id}")
        tprint(f"  Project : {self.project_name}")
        tprint(f"  Profile : {self.profile}")
        tprint(f"  Idea    : {product_idea[:80]}...")
        tprint("\n  Flow: PM(route) → [BA/Design/TechLead/test_plan/Dev/Test] per kind\n")

        if not self.maintain_mode:
            self._detect_maintain_from_task(product_idea)

        # ── PM router runs BEFORE BA clarification so Investigation kind
        #    can skip the heavy clarification gate.
        route = self._run_pm_router(product_idea)

        if route.kind == "investigation":
            if self.maintain_mode and self._maintain_dir:
                self._load_project_context(self._maintain_dir, task_hint=product_idea)
            self._run_investigation_path(product_idea)
            # Light-weight wrap-up (skip rule/skill optimizers — nothing to score).
            self._save_conversations()
            self.bus.print_log()
            tprint(f"\n{'✅'*5}  INVESTIGATION COMPLETE  {'✅'*5}")
            tprint(self.tokens.full_report())
            return self.results

        # Non-investigation: fall through to the full task-based flow, but
        # restrict which steps run per PM's dispatch plan.
        product_idea = self._clarification_gate(product_idea)

        if self.maintain_mode and self._maintain_dir:
            self._load_project_context(self._maintain_dir, task_hint=product_idea)

        self._run_task_based_pipeline(
            product_idea,
            resources=resources,
            allowed_steps=route.dispatch_steps(),
            pm_route=route,
        )

        self._apply_outcome_adjustments(product_idea)
        self._run_rule_optimizer()
        self._run_skill_optimizer()
        return self.results

    def _apply_outcome_adjustments(self, task: str):
        """Adjust critic scores based on real outcomes (tests, clarifications, cost)."""
        try:
            from learning.score_adjuster import ScoreAdjuster, count_clarifications_from_bus, count_missing_info
            from learning.skill_selector import detect_scope
        except ImportError:
            return
        if not self.critic_reviews:
            return

        adj = ScoreAdjuster()

        # 1. Test-informed (Patrol + Maestro)
        adj.apply_test_outcomes(
            self.critic_reviews,
            patrol_result=getattr(self, "_last_patrol_result", None),
            maestro_result=getattr(self, "_last_maestro_result", None),
        )

        # 2. Downstream signals (clarifications + MISSING_INFO leakage)
        dev_output = self.results.get("dev", "")
        missing_by_agent = count_missing_info(dev_output)
        downstream_signals: dict[str, dict] = {}
        role_map = {
            "ba": "Business Analyst (BA)", "techlead": "Tech Lead",
            "design": "UI/UX Designer", "dev": "Developer", "test": "QA/Tester",
        }
        # For each upstream agent, count how many downstream agents asked them
        for upstream in ("ba", "techlead", "design"):
            up_role = role_map[upstream]
            total_asks = sum(
                count_clarifications_from_bus(self.bus, asker_role, up_role)
                for asker_role in role_map.values() if asker_role != up_role
            )
            downstream_signals[upstream] = {
                "clarif_count":           total_asks,
                "missing_info_downstream": missing_by_agent.get(upstream, 0),
            }
        adj.apply_downstream_signals(self.critic_reviews, downstream_signals)

        # 3. Cost penalty using detected scope
        scope = detect_scope(task, project_context="")
        tokens_by_agent: dict[str, int] = {}
        for rec in self.tokens.records:
            tokens_by_agent[rec.agent] = tokens_by_agent.get(rec.agent, 0) + rec.total
        adj.apply_cost_penalty(self.critic_reviews, tokens_by_agent, scope)

        if adj.adjustments:
            tprint(f"\n  {'═'*60}")
            tprint(f"  ⚖️  SCORE ADJUSTMENTS ({len(adj.adjustments)}) — reflecting real outcomes")
            tprint(f"  {'═'*60}")
            for a in adj.adjustments[:10]:
                tprint(f"  [{a['agent_key'].upper()}] {a['kind']}: {a['detail']}")
            tprint(f"  {'─'*60}")
            tprint(f"  Adjusted scores will feed into rule_optimizer + skill_optimizer.")

    def _clarification_gate(self, product_idea: str) -> str:
        """BA checks if input is clear. If not, ask user clarifying questions."""
        if self._step_done("ba"):
            return product_idea  # already done, skip gate

        tprint(f"\n  {'─'*60}")
        tprint(f"  🎯 CLARIFICATION GATE — BA evaluating request...")
        tprint(f"  {'─'*60}")

        ba = self.agents["ba"]
        result = ba.check_clarity(product_idea)

        if result["is_clear"]:
            tprint(f"  ✅ Request is clear — proceeding.\n")
            return product_idea

        tprint(f"  ⚠️  Request is ambiguous. BA needs more info:\n")
        qa_pairs = []
        for i, q in enumerate(result["questions"], 1):
            tprint(f"  {i}. {q}")
            answer = input(f"     Answer: ").strip()
            if answer:
                qa_pairs.append({"q": q, "a": answer})

        if qa_pairs:
            enriched = ba.enrich_input(product_idea, qa_pairs)
            tprint(f"\n  ✅ Added {len(qa_pairs)} clarification(s) — proceeding with pipeline.\n")
            return enriched

        tprint(f"\n  ⚠️  No answer — continuing with current info.\n")
        return product_idea

    def run_update(self, task: str, source_session: str) -> dict:
        """
        Update mode: read existing docs from source_session, assess impact,
        re-run only affected steps. Unchanged steps load from existing docs.
        """
        tprint(f"\n{'🔄'*5}  UPDATE MODE  {'🔄'*5}")
        tprint(f"  Task    : {task[:80]}")
        tprint(f"  Source  : {source_session}")

        # Load all existing docs from the source session
        existing: dict[str, str] = {}
        for key in self.STEP_KEYS:
            path = self.output_dir / f"{source_session}_{key}.md"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                # Strip checkpoint header
                existing[key] = content.split("---", 1)[1].strip() if "---" in content else content
                tprint(f"  📄 Loaded: {key} ({len(existing[key])} chars)")
            else:
                tprint(f"  ⚠️  Missing: {key}")

        if not existing:
            tprint("  ❌ No docs found — running fresh pipeline instead.")
            return self.run(task)

        # BA assesses impact
        tprint(f"\n  {'─'*60}")
        tprint(f"  🎯 BA evaluating impact...")
        ba = self.agents["ba"]
        ba._current_step = "impact_assessment"
        assessment = ba.assess_impact(task, existing)
        ba.print_impact(assessment)

        # Confirm with user
        tprint(f"\n  Continue? [Enter] confirm / [E] edit affected steps list")
        choice = input("  > ").strip().upper()
        if choice == "E":
            raw = input(f"  Re-enter affected steps (e.g.: techlead,dev,test): ").strip()
            assessment["affected"]  = [s.strip() for s in raw.split(",") if s.strip()]
            assessment["unchanged"] = [s for s in self.STEP_KEYS if s not in assessment["affected"]]

        # Pre-load unchanged steps from existing docs
        for key in assessment["unchanged"]:
            if key in existing:
                self.results[key] = existing[key]
                tprint(f"  ✅ [{key:8}] Unchanged from session {source_session}")

        # Inject existing docs as context for affected agents
        existing_context = "\n\n".join(
            f"## Existing {k.upper()}\n{v[:1500]}"
            for k, v in existing.items()
            if k in assessment["unchanged"]
        )
        for key in assessment["affected"]:
            agent = self.agents.get(key)
            if agent:
                agent.project_context = (
                    f"## EXISTING DOCS (unchanged — use as context, do not duplicate)\n"
                    f"{existing_context[:3000]}\n\n"
                    + (agent.project_context or "")
                )

        # Clarification gate then run pipeline (skips pre-loaded steps)
        task = self._clarification_gate(task)
        self._run_task_based_pipeline(task)
        self._run_rule_optimizer()
        return self.results

    def run_feedback(self, source_session: str, feedback: dict) -> dict:
        """
        Feedback mode: collect issue from real product → re-run only affected steps.
        feedback: {type, description, screenshot_path?}
        """
        tprint(f"\n{'💬'*5}  FEEDBACK MODE  {'💬'*5}")
        tprint(f"  Type    : {feedback['type']}")
        tprint(f"  Session : {source_session}")
        tprint(f"  Issue   : {feedback['description'][:80]}")

        # Load existing docs
        existing: dict[str, str] = {}
        for key in self.STEP_KEYS:
            path = self.output_dir / f"{source_session}_{key}.md"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                existing[key] = content.split("---", 1)[1].strip() if "---" in content else content
                tprint(f"  📄 Loaded: {key} ({len(existing[key])} chars)")

        if not existing:
            tprint("  ❌ No docs found — running fresh pipeline instead.")
            return self.run(feedback["description"])

        # Screenshot analysis (UX issues / bugs with visual evidence)
        screenshot_analysis = ""
        screenshot_path = feedback.get("screenshot_path", "")
        if screenshot_path:
            tprint(f"\n  🖼️  Analyzing screenshot: {screenshot_path}")
            des = self.agents["design"]
            design_doc = existing.get("design", "")
            try:
                screenshot_analysis = des._call_with_image(
                    "Analyze screenshot from sản phẩm thực. So sánh with design specs and mô tả issue from user.",
                    f"Design specs:\n{design_doc[:1500]}\n\nUser mô tả issue: {feedback['description']}",
                    screenshot_path,
                )
                tprint(f"  ✅ Screenshot analyzed.")
                tprint(f"     {screenshot_analysis[:120]}...")
            except Exception as e:
                tprint(f"  ⚠️  Screenshot analysis failed: {e}")

        # Build feedback task description
        task_parts = [f"[{feedback['type']}] {feedback['description']}"]
        if screenshot_analysis:
            task_parts.append(f"\nUI Analysis from screenshot:\n{screenshot_analysis}")
        task = "\n".join(task_parts)

        # BA assesses which steps are affected
        tprint(f"\n  {'─'*60}")
        tprint(f"  🎯 BA evaluating feedback impact...")
        ba = self.agents["ba"]
        ba._current_step = "feedback_assessment"
        assessment = ba.assess_feedback(task, existing, feedback["type"])
        ba.print_impact(assessment)

        # Confirm with user
        tprint(f"\n  Continue? [Enter] confirm / [E] edit affected steps list")
        choice = input("  > ").strip().upper()
        if choice == "E":
            raw = input(f"  Re-enter affected steps (e.g.: dev,test): ").strip()
            assessment["affected"]  = [s.strip() for s in raw.split(",") if s.strip()]
            assessment["unchanged"] = [s for s in self.STEP_KEYS if s not in assessment["affected"]]

        # Pre-load unchanged steps
        for key in assessment["unchanged"]:
            if key in existing:
                self.results[key] = existing[key]
                tprint(f"  ✅ [{key:12}] Unchanged")

        # Inject feedback context into affected agents
        feedback_context = (
            f"## 💬 PRODUCT FEEDBACK (from sản phẩm thực tế)\n"
            f"**Type:** {feedback['type']}\n"
            f"**Description:** {feedback['description']}\n"
            + (f"**Screenshot Analysis:** {screenshot_analysis}\n" if screenshot_analysis else "")
            + f"\n## Existing Docs (unchanged — use do context)\n"
            + "\n\n".join(
                f"### {k.upper()}\n{v[:800]}"
                for k, v in existing.items()
                if k in assessment["unchanged"]
            )
        )
        for key in assessment["affected"]:
            agent = self.agents.get(key)
            if agent:
                agent.project_context = feedback_context[:3000] + "\n\n" + (agent.project_context or "")

        task = self._clarification_gate(task)
        self._run_task_based_pipeline(task)
        self._run_rule_optimizer()
        return self.results

    def run_resume(self) -> dict:
        completed = [k for k in self.STEP_KEYS if self._step_done(k)]
        missing   = [k for k in self.STEP_KEYS if not self._step_done(k)]
        tprint(f"\n{'🔄'*5}  RESUME PIPELINE  {'🔄'*5}")
        tprint(f"  Session  : {self.session_id}")
        tprint(f"  ✅ Done  : {', '.join(completed) or 'none'}")
        tprint(f"  ⏳ Resume: {', '.join(missing) or 'none'}\n")
        # Reload the BA output so we can reconstruct task list and continue
        ba_md = self.results.get("ba", "")
        self._run_task_based_pipeline(product_idea=ba_md[:400] or "resume")
        self._run_rule_optimizer()
        return self.results

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _run_rule_evolver(self, raw_suggestions: list[dict], history):
        """Product/enterprise rule evolution: provenance + multi-dim + A/B.

        Opt-in via MULTI_AGENT_RULE_EVOLVER=1. Supersedes the legacy
        auto-apply loop in _run_rule_optimizer by routing suggestions
        through RuleEvolver (which merges with user feedback + cost
        signals and can shadow rules for A/B testing).
        """
        from learning.rule_evolver import (
            RuleEvolver, Suggestion, SRC_LLM, SRC_INTEGRITY,
        )
        from agents.base_agent import _RULES_DIR

        profile_dir = _RULES_DIR / self.profile
        evolver = RuleEvolver(profile_dir, session_id=self.session_id)

        # Split incoming raw suggestions by source tag we embedded earlier.
        llm_raw:      list[dict] = []
        integrity_raw: list[dict] = []
        for s in raw_suggestions:
            if s.get("source") == "integrity":
                integrity_raw.append(s)
            else:
                llm_raw.append(s)

        merged = evolver.gather(
            llm_suggestions=llm_raw,
            integrity_suggestions=integrity_raw,
        )
        if not merged:
            tprint("  RuleEvolver: no signals → skip this session.")
        else:
            # Load current rule content for consistency scoring.
            current: dict[str, str] = {}
            for s in merged:
                path = self._rule_path_for(s.agent_key, s.target_type)
                try:
                    current[f"{s.agent_key}:{s.target_type}"] = path.read_text(encoding="utf-8") if path.exists() else ""
                except Exception:
                    current[f"{s.agent_key}:{s.target_type}"] = ""

            decided = evolver.decide(merged, current)

            # Filter suggestions that ReviseHistory blacklists / conflicts.
            filtered = []
            for s in decided:
                if history.is_blacklisted(s.agent_key, s.reason, s.target_type):
                    tprint(f"  🚫 [{s.agent_key.upper()}] Skip — blacklisted regression pattern.")
                    continue
                conflict = history.conflicts_with_pass_patterns(s.agent_key, s.addition)
                if conflict:
                    tprint(f"  🛡️  [{s.agent_key.upper()}] Skip — conflicts PASS pattern.")
                    continue
                filtered.append(s)

            result = evolver.apply(filtered, self._rule_path_for)

            tprint(f"\n  {'═'*60}")
            tprint(f"  🧬 RULE EVOLVER")
            tprint(f"  {'═'*60}")
            tprint(f"  ✅ auto-applied : {len(result['applied'])}")
            tprint(f"  🧪 shadowed A/B : {len(result['shadowed'])}")
            tprint(f"  ⏳ pending     : {len(result['pending'])}")
            for item in result["applied"][:5]:
                src = "+".join(item["sources"])
                tprint(f"     ✅ [{item['agent_key'].upper()}] "
                       f"src={src} score={item['multi_dim']:.2f} — "
                       f"{item['reason'][:70]}")
            for item in result["shadowed"][:5]:
                tprint(f"     🧪 [{item['agent_key'].upper()}] shadow — "
                       f"{item['reason'][:70]}")

        # Evaluate live shadows (promote/demote based on accumulated scores).
        actions = evolver.evaluate_shadows(self._rule_path_for)
        promoted = [a for a in actions if a["action"] == "promote"]
        demoted  = [a for a in actions if a["action"] == "demote"]
        if promoted or demoted:
            tprint(f"\n  🔬 Shadow verdicts: "
                   f"{len(promoted)} promoted, {len(demoted)} demoted")
            for a in promoted:
                tprint(f"     ⬆️  PROMOTE {a['key']} (+{a['delta']:.2f})")
            for a in demoted:
                tprint(f"     ⬇️  DEMOTE  {a['key']} ({a['delta']:.2f})")

    def _rule_path_for(self, agent_key: str, target_type: str):
        """Resolve rules/<profile>/<agent>.md vs rules/<profile>/criteria/<agent>.md."""
        from agents.base_agent import _RULES_DIR
        from pathlib import Path as _P
        base = _RULES_DIR / self.profile
        if target_type == "criteria":
            return _P(base / "criteria" / f"{agent_key}.md")
        return _P(base / f"{agent_key}.md")

    def _run_rule_optimizer(self):
        """After pipeline, auto-apply recurring improvements; ask user only for new patterns."""
        from learning.revise_history import ReviseHistory, AUTO_THRESHOLD

        # Profile-level history — shared across all projects using the same profile
        from agents.base_agent import _RULES_DIR
        history_path = _RULES_DIR / self.profile / ".revise_history.json"
        history = ReviseHistory(history_path)

        # Record score trend + PASS patterns + checklist answers for every agent reviewed
        for r in self.critic_reviews:
            if r.get("score") is not None:
                history.record_score(r["agent_key"], float(r["score"]), self.session_id)
            if r.get("verdict") == "PASS" and r.get("strengths"):
                history.record_pass(r["agent_key"], r["strengths"], self.session_id)
            if r.get("checklist_flat") and r.get("checklist_answers"):
                history.record_checklist_answers(
                    r["agent_key"], r["checklist_flat"], r["checklist_answers"], self.session_id
                )

        # Collect easy items (100% YES across 5+ sessions) — criteria need siết
        easy_items: list[dict] = []
        for key in set(r["agent_key"] for r in self.critic_reviews):
            easy_items.extend(history.get_easy_items(key))

        revise_reviews = [r for r in self.critic_reviews if r["verdict"] == "REVISE"]

        # Collect chronic patterns: count >= 2, not yet applied — across all sessions
        chronic_patterns = [
            entry for entry in history._data.values()
            if isinstance(entry, dict)
            and not entry.get("agent_key", "").startswith("__")  # skip trend entries
            and entry.get("count", 0) >= 2
            and not entry.get("applied", False)
        ]
        # Sort by count descending — most recurring first
        chronic_patterns.sort(key=lambda e: e["count"], reverse=True)

        if not revise_reviews and not chronic_patterns and not easy_items:
            tprint("\n  🧠 Rule Optimizer: none REVISE nào and none bug lặp again — rules  tốt, skip.")
            self._print_score_trends(history)
            return

        tprint(f"\n{'─'*70}")
        label_parts = []
        if revise_reviews:
            label_parts.append(f"{len(revise_reviews)} REVISE session này")
        if chronic_patterns:
            label_parts.append(f"{len(chronic_patterns)} bug lặp again from history")
        tprint(f"  🧠 RULE OPTIMIZER — analyze {' + '.join(label_parts)}...")
        if chronic_patterns:
            tprint(f"  📚 Chronic patterns (top 3):")
            for p in chronic_patterns[:3]:
                tprint(f"     [{p['agent_key'].upper()}] {p['count']}x — {p['reason_sample'][:70]}")
        tprint(f"{'─'*70}")

        self._print_score_trends(history)

        if easy_items:
            tprint(f"  🟡 Easy items (luôn YES ≥5 sessions — need siết): {len(easy_items)} items")
            for ei in easy_items[:3]:
                tprint(f"     [{ei['agent_key'].upper()}] ({ei['total_count']}x) {ei['sample'][:70]}")

        suggestions = self.rule_optimizer.analyze_and_suggest(
            revise_reviews, chronic_patterns, history=history, easy_items=easy_items
        )
        # Add deterministic IntegrityRules-driven suggestions. These are
        # "yesterday's lessons": modules with persistent failures, keyword
        # risk promotions, and agent reputation penalties — translated into
        # concrete rule-file edits without any extra LLM cost.
        integrity_suggestions = self.rule_optimizer.suggest_from_integrity(
            getattr(self, "_integrity", None)
        )
        if integrity_suggestions:
            tprint(f"\n  🧬 Integrity surface {len(integrity_suggestions)} "
                   f"deterministic rule suggestion(s) (no LLM cost)")
            suggestions = list(suggestions or []) + integrity_suggestions

        # ── Learning system: unified rule evolution (default ON) ─────────────
        # The learning system natively covers: LLM+Integrity+UserFeedback+Cost
        # signal merge, provenance tagging, multi-dim scoring, A/B shadows,
        # and promote/demote verdicts. Legacy auto-apply-on-repeat is kept
        # behind MULTI_AGENT_LEGACY_RULE_OPTIMIZER=1 as an escape hatch.
        use_legacy = os.environ.get("MULTI_AGENT_LEGACY_RULE_OPTIMIZER", "0") == "1"
        if not use_legacy:
            self._run_rule_evolver(suggestions, history)
            return

        if not suggestions:
            tprint("  No tìm thấy pattern bug rõ ràng to cải tcurrent.")
            return

        auto_applied: list[tuple] = []
        pending: list[tuple] = []

        for s in suggestions:
            if history.is_blacklisted(s["agent_key"], s["reason"], s["target_type"]):
                tprint(f"  🚫 [{s['agent_key'].upper()}] Skip — pattern  bị blacklist after regression.")
                continue
            # Check conflict with known good (PASS) patterns
            conflict = history.conflicts_with_pass_patterns(s["agent_key"], s["addition"])
            if conflict:
                tprint(f"  🛡️  [{s['agent_key'].upper()}] Skip — xung đột with pattern PASS  tốt:")
                tprint(f"       \"{conflict[:80]}\"")
                continue
            count = history.record(s["agent_key"], s["reason"], s["addition"], s["target_type"])
            if history.should_auto_apply(s["agent_key"], s["reason"], s["target_type"]):
                backup_path = self.rule_optimizer.apply(s)
                if backup_path:
                    history.mark_applied(s["agent_key"], s["reason"], s["target_type"],
                                         backup_path=backup_path, apply_session_id=self.session_id)
                    auto_applied.append((s, count))
            else:
                pending.append((s, count))

        if auto_applied:
            tprint(f"\n  {'═'*60}")
            tprint(f"  🤖 AUTO-APPLIED {len(auto_applied)} cải tcurrent (pattern ≥{AUTO_THRESHOLD} time)")
            tprint(f"  {'═'*60}")
            for s, count in auto_applied:
                icon = "📋" if s["target_type"] == "criteria" else "📜"
                action_label = f"[{s.get('action','ADD')}]"
                tprint(f"  {icon} {action_label} [{s['agent_key'].upper()}] ({count}x) {s['reason'][:80]}")
                tprint(f"     + {s['addition'][:100]}")
                tprint(f"     ↳ rules/backups/ có bản backup")

        # ── Regression check for previously applied rules ─────────────────────
        from agents.base_agent import _RULES_DIR as _RD
        regressed = []
        for entry in history.get_applied_entries():
            agent_key      = entry["agent_key"]
            apply_sid      = entry.get("apply_session_id", "")
            backup_path    = entry.get("backup_path", "")
            if not apply_sid or not backup_path:
                continue
            if history.detect_regression(agent_key, apply_sid):
                regressed.append((entry, backup_path))

        if regressed:
            tprint(f"\n  {'═'*60}")
            tprint(f"  🔄 REGRESSION DETECTED — tự động rollback {len(regressed)} rule(s)")
            tprint(f"  {'═'*60}")
            for entry, backup_path in regressed:
                agent_key   = entry["agent_key"]
                target_type = entry["target_type"]
                if target_type == "criteria":
                    rule_path = _RD / self.profile / "criteria" / f"{agent_key}.md"
                else:
                    rule_path = _RD / self.profile / f"{agent_key}.md"

                ok = self.rule_optimizer.rollback(backup_path, rule_path)
                if ok:
                    history.mark_failed(agent_key, entry["reason_sample"], target_type)
                    history.mark_rolled_back(entry["_key"])
                    tprint(f"  ↩️  [{agent_key.upper()}] Rule  rollback về bản cũ + blacklist pattern.")
                else:
                    tprint(f"  ❌ [{agent_key.upper()}] Rollback thất bại — kiểm tra backup thủ công: {backup_path}")

        # ── Criteria upgrade when agent liên tục PASS cao ─────────────────────
        from agents.base_agent import _RULES_DIR as _RD
        import re as _re

        upgraded = []
        for key in set(r["agent_key"] for r in self.critic_reviews):
            if not history.should_upgrade_criteria(key):
                continue

            crit_path = _RD / self.profile / "criteria" / f"{key}.md"
            if not crit_path.exists():
                crit_path = _RD / "default" / "criteria" / f"{key}.md"
            if not crit_path.exists():
                continue

            content = crit_path.read_text(encoding="utf-8")
            m = _re.search(r"PASS_THRESHOLD:\s*(\d+)", content)
            if not m:
                continue

            old_threshold = int(m.group(1))
            new_threshold = min(old_threshold + 1, 10)
            if new_threshold == old_threshold:
                continue

            # Backup + update
            backup_dir = _RD / "backups"
            backup_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy(crit_path, backup_dir / f"criteria_{key}_upgrade_{ts}.md")

            new_content = _re.sub(
                r"PASS_THRESHOLD:\s*\d+",
                f"PASS_THRESHOLD: {new_threshold}",
                content,
            )
            # Write to active profile dir
            active_path = _RD / self.profile / "criteria" / f"{key}.md"
            active_path.parent.mkdir(parents=True, exist_ok=True)
            active_path.write_text(new_content, encoding="utf-8")

            history.mark_upgraded(key)
            upgraded.append((key, old_threshold, new_threshold))

        if upgraded:
            tprint(f"\n  {'═'*60}")
            tprint(f"  📈 CRITERIA UPGRADED — agent liên tục đạt ≥{history.UPGRADE_AVG_THRESHOLD} in {history.UPGRADE_MIN_SESSIONS} sessions")
            tprint(f"  {'═'*60}")
            for key, old, new in upgraded:
                tprint(f"  ⬆️  [{key.upper()}] PASS_THRESHOLD: {old} → {new}")
                tprint(f"     (backup lưu in rules/backups/)")

        # Cleanup old applied entries (>30 days)
        cleaned = history.cleanup(days=30)
        if cleaned:
            tprint(f"\n  🧹 Done dọn {cleaned} pattern cũ (applied >30 day) khỏi history.")

        for s, count in pending:
            self.rule_optimizer.print_suggestion(s)
            tprint(f"  (pattern xuất current {count}/{AUTO_THRESHOLD} time — not yet enough to tự động)")
            answer = input(f"\n  Áp dụng cải tcurrent? [y/N] ").strip().lower()
            if answer == "y":
                self.rule_optimizer.apply(s)
                history.mark_applied(s["agent_key"], s["reason"], s["target_type"])
                tprint(f"  ✅ Done update (backup lưu in rules/backups/)")
            else:
                tprint(f"  ⏭️  Skip.")

    def _run_skill_optimizer(self):
        """Meta-learn at skill granularity: CREATE / REFINE / MERGE / Shadow-promote / Deprecate."""
        try:
            from learning.skill_optimizer import SkillOptimizer
        except ImportError:
            return

        opt = SkillOptimizer(profile=self.profile)
        skill_usage: list[dict] = []
        for agent in list(self.agents.values()) + [self.critic, self.investigator]:
            skill_usage.extend(getattr(agent, "_skill_usage_log", []) or [])

        opt.record_from_critic_reviews(self.critic_reviews, skill_usage, self.session_id)
        opt.print_stats()

        # 1️⃣ Judge shadow skills first (A/B decisions)
        self._judge_shadow_skills(opt)

        # 2️⃣ Deprecate chronically underperforming skills (safeguarded)
        deprecated = opt.deprecate_underperforming()
        if deprecated:
            tprint(f"\n  ❌ Skills deprecated (avg < 5.0 across ≥5 uses):")
            for agent_key, skill, avg in deprecated:
                tprint(f"     {agent_key}/{skill} (avg={avg:.1f}) → .md.deprecated")

        # 3️⃣ Suggest REFINE for mid-score skills
        refinements = opt.suggest_refinements()
        for r in refinements[:2]:  # max 2 refines per session
            self._apply_refine(opt, r)

        # 4️⃣ Suggest NEW skills for chronic misfit (with dedupe & preview)
        creations = opt.suggest_new_skills()
        for s in creations[:2]:  # max 2 new skills per session
            self._apply_create(opt, s)

        # 5️⃣ Suggest MERGE for near-duplicate skills
        merges = opt.suggest_merges()
        for m in merges[:1]:  # max 1 merge per session
            self._apply_merge(opt, m)

    # ── Shadow A/B judge ──────────────────────────────────────────────────────

    def _judge_shadow_skills(self, optimizer):
        ready = optimizer.history.shadows_ready_to_judge()
        if not ready:
            return
        tprint(f"\n  🔬 Shadow A/B judge — {len(ready)} skill(s) enough dữ liệu to quyết định:")
        for r in ready:
            shadow = r["shadow_entry"]
            delta  = r["delta"]
            icon   = "✅" if delta >= 0.5 else ("≈" if delta >= -0.3 else "❌")
            tprint(f"     {icon} {shadow['agent_key']}/{shadow['skill_key']} "
                   f"shadow={r['shadow_avg']:.1f}  parent={r['parent_avg']:.1f}  Δ={delta:+.1f}")
            if delta >= 0.5:  # margin required
                optimizer.promote_shadow(shadow)
                tprint(f"       → PROMOTED: shadow tor thế parent")
            else:
                optimizer.demote_shadow(shadow)
                tprint(f"       → REJECTED: shadow bị rollback, giữ parent")

    # ── REFINE ────────────────────────────────────────────────────────────────

    def _apply_refine(self, optimizer, suggestion: dict):
        agent_key = suggestion["agent_key"]
        skill_key = suggestion["skill_key"]
        avg       = suggestion["avg_score"]

        skill_path = Path(__file__).parent / "skills" / agent_key / f"{skill_key}.md"
        if not skill_path.exists():
            return
        current_content = skill_path.read_text(encoding="utf-8")

        # Collect recent weaknesses for this agent in this session
        weaknesses = []
        for r in self.critic_reviews:
            if r["agent_key"] == agent_key and r.get("weaknesses"):
                weaknesses.extend(r["weaknesses"][:3])

        tprint(f"\n  🔧 REFINE candidate: {agent_key}/{skill_key} avg={avg:.1f}")
        if weaknesses:
            tprint(f"     Weaknesses:")
            for w in weaknesses[:3]:
                tprint(f"       • {w[:90]}")

        self.skill_designer._current_step = "skill_refine"
        try:
            result = self.skill_designer.refine_existing(
                agent_key, skill_key, current_content, weaknesses, avg,
            )
        except Exception as e:
            tprint(f"     ❌ Refine failed: {e}")
            return

        if not result["ok"]:
            tprint(f"     ⏭  Abort: {result.get('reason', 'no reason')}")
            return

        if result["confidence"] == "LOW":
            tprint(f"     ⏭  Confidence LOW — skip ({result.get('rationale', '')})")
            return

        # Write as shadow
        new_path, backup = optimizer.refine_skill(agent_key, skill_key, result["content"])
        tprint(f"     🆕 Shadow written → {new_path.name}  (backup: {Path(backup).name})")
        tprint(f"     ℹ️  Will A/B test qua 2 session tới before when promote.")

    # ── CREATE ────────────────────────────────────────────────────────────────

    def _apply_create(self, optimizer, suggestion: dict):
        """
        Auto-create new skill for chronic misfit pattern.

        Gated by:
          - Pattern must have recurred ≥ NEW_SKILL_THRESHOLD sessions (handled upstream)
          - SkillDesigner must return confidence != LOW
          - Writes as `shadow` — auto-promote/demote after 2 more sessions of A/B

        User review only if MULTI_AGENT_SKILL_REVIEW=1 (default: auto).
        """
        import os as _os
        agent_key = suggestion["agent_key"]
        proposed  = suggestion["proposed_skill_key"]
        pattern   = suggestion["pattern"]
        task_smp  = suggestion["task_sample"]

        tprint(f"\n  💡 NEW SKILL auto-create: [{agent_key.upper()}] → {proposed}")
        tprint(f"     Pattern recurred {suggestion['count']} sessions: {pattern[:90]}")

        self.skill_designer._current_step = "skill_create"
        try:
            result = self.skill_designer.design_new_skill(agent_key, proposed, pattern, task_smp)
        except Exception as e:
            tprint(f"     ❌ Design failed: {e}")
            return

        if not result["ok"]:
            tprint(f"     ⏭  SkillDesigner aborted: {result.get('reason', 'no reason')}")
            return

        if result["confidence"] == "LOW":
            tprint(f"     ⏭  Confidence LOW — skip (tránh bloat skill list)")
            tprint(f"        Rationale: {result.get('rationale', '-')}")
            return

        # Optional manual review — opt-in via env var
        if _os.environ.get("MULTI_AGENT_SKILL_REVIEW", "0") == "1":
            tprint(f"     {'─'*60}")
            for line in result["content"].splitlines()[:25]:
                tprint(f"     {line}")
            if result["content"].count("\n") > 25:
                tprint(f"     ... (+{result['content'].count(chr(10)) - 25} more lines)")
            tprint(f"     Confidence: {result['confidence']} — {result.get('rationale', '')}")
            tprint(f"     {'─'*60}")
            confirm = input(f"     Ghi file? [Y/n] ").strip().lower()
            if confirm == "n":
                tprint(f"     ⏭  User rejected.")
                return

        # Auto-write as shadow — shadow A/B judge will tự promote/demote after 2 uses
        path = optimizer.write_new_skill(
            agent_key, proposed, result["content"], shadow_for=None,
        )
        optimizer.history.mark_status(agent_key, proposed, "shadow")
        tprint(f"     ✅ Shadow skill → {path.name}")
        tprint(f"     ⚖️  A/B judge: will quyết định after {2} uses more")

    # ── MERGE ─────────────────────────────────────────────────────────────────

    def _apply_merge(self, optimizer, suggestion: dict):
        """
        Auto-merge near-duplicate skills (trigger overlap ≥ 70%).
        Merged file written as shadow; one original retired immediately.
        A/B judge will promote if merged beats remaining original.

        Opt-in manual review via MULTI_AGENT_SKILL_REVIEW=1.
        """
        import os as _os
        agent_key = suggestion["agent_key"]
        a = suggestion["skill_a"]; b = suggestion["skill_b"]
        tprint(f"\n  🔗 MERGE auto-applied: {agent_key}  {a} ↔ {b}  "
               f"overlap={suggestion['overlap']:.0%}")

        from pathlib import Path as _P
        skills_dir = _P(__file__).parent / "skills" / agent_key
        a_path = skills_dir / f"{a}.md"
        b_path = skills_dir / f"{b}.md"
        if not (a_path.exists() and b_path.exists()):
            return

        try:
            result = self.skill_designer.design_merge(
                agent_key,
                a_path.read_text(encoding="utf-8"),
                b_path.read_text(encoding="utf-8"),
            )
        except Exception as e:
            tprint(f"     ❌ Merge design failed: {e}")
            return
        if not result["ok"]:
            tprint(f"     ⏭  SkillDesigner aborted: {result.get('reason', '')}")
            return

        if _os.environ.get("MULTI_AGENT_SKILL_REVIEW", "0") == "1":
            tprint(f"     {'─'*60}")
            for line in result["content"].splitlines()[:20]:
                tprint(f"     {line}")
            tprint(f"     {'─'*60}")
            confirm = input(f"     Apply merge? [Y/n] ").strip().lower()
            if confirm == "n":
                tprint(f"     ⏭  User rejected.")
                return

        merged_key = optimizer._unique_key(agent_key, f"{a}_merged")
        optimizer.write_new_skill(agent_key, merged_key, result["content"], shadow_for=a)
        # Retire b immediately (shadow covers its scope)
        b_path.rename(b_path.with_suffix(".merged.md"))
        optimizer.history.mark_status(agent_key, b, "retired_merged")
        tprint(f"     🧩 Merged → {merged_key}.md (shadow). {b}.md → .merged.md")

    def _print_score_trends(self, history):
        """Show per-agent score trends with sparklines across sessions."""
        _SPARK = " ▁▂▃▄▅▆▇█"

        def _sparkline(scores: list[float]) -> str:
            if not scores:
                return ""
            lo, hi = min(scores), max(scores)
            span = hi - lo or 1
            return "".join(_SPARK[min(8, int((s - lo) / span * 8))] for s in scores)

        agent_keys = [r["agent_key"] for r in self.critic_reviews]
        seen: set[str] = set()
        rows = []
        for key in agent_keys:
            if key in seen:
                continue
            seen.add(key)
            entry = history._data.get(f"__trend__{key}", {})
            scores_raw = [s["score"] for s in entry.get("scores", [])]
            if len(scores_raw) < 2:
                continue
            recent = scores_raw[-10:]
            spark  = _sparkline(recent)
            trend  = history.score_trend(key)
            rows.append((key, spark, trend))

        # Show PASS patterns being protected
        protected: list[str] = []
        for key in seen:
            patterns = history.get_pass_patterns(key)
            if patterns:
                top = patterns[0]["sample"][:60]
                protected.append(f"[{key}] {top}")
        if protected:
            tprint(f"\n  🛡️  PASS patterns  bảo vệ ({len(protected)}):")
            for p in protected[:4]:
                tprint(f"     • {p}")

        if not rows:
            return

        tprint(f"\n  📈 Score trends — profile: {self.profile}")
        tprint(f"  {'─'*56}")
        for key, spark, trend in rows:
            tprint(f"  [{key:8}]  {spark}  {trend}")
        tprint(f"  {'─'*56}")

        # Mini pipeline diagram with latest scores
        step_scores = {}
        for r in self.critic_reviews:
            step_scores.setdefault(r["agent_key"], []).append(r["score"])
        avg = {k: sum(v) / len(v) for k, v in step_scores.items()}

        def _node(key: str) -> str:
            s = avg.get(key)
            if s is None:
                return f"[ {key[:2].upper()} ]"
            icon = "✅" if s >= 7 else "⚠️ "
            return f"{icon}{key[:2].upper()}({s:.1f})"

        pipeline_keys = ["ba", "design", "techlead", "dev", "test"]
        nodes = " ──► ".join(_node(k) for k in pipeline_keys if k in avg)
        if nodes:
            tprint(f"\n  Pipeline scores này session:")
            tprint(f"  {nodes}")

    # ── QA → TechLead → Dev fix loop ─────────────────────────────────────────

    def _option_c_spec_postmortem(self, tl, ba, dev,
                                    blockers: list[str], tasks: list,
                                    implementation: str, product_idea: str) -> bool:
        """
        Option C — when Dev fix is stuck (same BLOCKER 2 rounds in a row), TL
        asks BA to reflect on whether the spec itself was incomplete. BA
        rewrites the affected tasks, Dev re-implements from the refined spec.

        Returns True if spec was actually refined and Dev re-ran; False if
        nothing useful came back and the caller should escalate.
        """
        self._dialogue_header("TL → BA spec postmortem (stuck loop)")

        stuck_ids = [t.id for t in (tasks or [])][:5]
        question = (
            "Dev + QA đã stuck 2 vòng fix với các BLOCKER lặp lại:\n\n"
            + "\n".join(f"- {b[:120]}" for b in blockers[:4])
            + f"\n\nTask liên quan (suy luận): {', '.join(stuck_ids) or '(không rõ)'}"
            + "\n\nSpec gốc có thiếu AC, edge case, hoặc business rule nào không?"
            + " Nếu có, nêu cụ thể — TL sẽ nhờ bạn viết lại spec."
        )
        try:
            ba_reflection = tl.ask(ba, question)
        except Exception as e:
            tprint(f"  ⚠️  TL → BA postmortem failed: {e}")
            return False

        if not ba_reflection or len(ba_reflection) < 40:
            tprint("  ℹ️  BA reflection too short — nothing to act on.")
            return False

        # Ask BA to rewrite the full task list with the postmortem applied.
        current_ba_md = self.results.get("ba", "")
        if not current_ba_md:
            return False
        if not hasattr(ba, "revise_specs"):
            return False
        try:
            new_ba_md = ba.revise_specs(current_ba_md, ba_reflection, stuck_ids)
        except Exception as e:
            tprint(f"  ⚠️  BA.revise_specs failed: {e}")
            return False
        if not new_ba_md or new_ba_md.strip() == current_ba_md.strip():
            tprint("  ℹ️  BA returned no meaningful changes — stopping postmortem.")
            return False

        # Persist the refined spec — resume/audit will see it.
        self.results["ba"] = new_ba_md
        self._save("ba", new_ba_md)
        tprint(f"  🔁 BA rewrote spec for stuck tasks — Dev re-implementing")

        # Dev re-implements using the refined spec as the fix guide.
        fix_guide = [
            "Spec was refined by BA after postmortem — re-implement the "
            "affected tasks per the NEW AC + edge cases below:",
            ba_reflection[:800],
        ]
        new_impl = dev.revise(implementation, fix_guide, product_idea)
        self._save("dev", new_impl)
        self.results["dev"] = new_impl
        return True

    def _qa_dev_loop(
        self,
        qa, dev, tl,
        test_plan_doc: str,
        implementation: str,
        dev_clarification: str,
        initial_review: str,
        product_idea: str,
        tasks: list | None = None,
    ) -> str:
        """
        QA finds BLOCKERs → reports to TechLead → TechLead triages → Dev fixes → QA re-verifies.
        Loops until:
          - No more BLOCKERs (success), OR
          - Same blockers appear 2 rounds in a row (no progress → escalate to user), OR
          - Token budget exhausted
        """
        review_output = initial_review
        prev_blocker_set: set[str] = set()
        round_num = 0
        audit_tasks = tasks or []

        while True:
            blockers = self._extract_blockers(review_output)
            if not blockers:
                tprint(f"\n  ✅ QA: No có BLOCKER — implementation đạt request.")
                break

            # ── Emergency Audit Mode: only on the first iteration, and only
            # when some upstream Critic was actually skipped for these tasks.
            if round_num == 0 and self._skipped_critic_by_task and not self._emergency_audit:
                self._trigger_emergency_audit(blockers, audit_tasks,
                                               agent_in_charge="Dev")

            round_num += 1
            tprint(f"\n  {'═'*60}")
            tprint(f"  🚨 QA→TechLead→Dev LOOP round {round_num} — {len(blockers)} BLOCKER(s):")
            for b in blockers[:4]:
                tprint(f"     • {b}")
            tprint(f"  {'═'*60}")

            # Detect no progress: same blockers as previous round
            current_set = set(b[:80] for b in blockers)
            spec_rescued = False
            if current_set == prev_blocker_set:
                # ── Option C — TL asks BA to reflect on spec before giving up.
                # Only fires once per session to avoid a BA ↔ TL chatter loop.
                if not getattr(self, "_ba_postmortem_fired", False):
                    spec_rescued = self._option_c_spec_postmortem(
                        tl, self.agents["ba"], dev, blockers, audit_tasks,
                        implementation, product_idea,
                    )
                    self._ba_postmortem_fired = True
                if not spec_rescued:
                    tprint(f"\n  ⚠️  BLOCKERs unchanged after round {round_num} — "
                           f"manual intervention needed.")
                    answer = input("  Keep trying to fix? [y/N] ").strip().lower()
                    if answer != "y":
                        tprint("  ⏹  Stopping loop — BLOCKERs unresolved.")
                        break
            prev_blocker_set = current_set

            if not self._check_quota(f"QA→TechLead→Dev fix round {round_num}"):
                break

            # QA báo TechLead → TechLead triage and giao fix task for Dev
            self._dialogue_header(f"QA reports bugs to Tech Lead (round {round_num})")
            tl_fix_assignment = tl.triage_bugs(dev, blockers, implementation)

            # Auto-inject missing widget keys BEFORE generic revise
            missing_keys = self._extract_missing_widget_keys(review_output)
            if missing_keys and hasattr(dev, "inject_widget_keys"):
                tprint(f"\n  🔑 Auto-inject {len(missing_keys)} missing widget keys:")
                for k in missing_keys[:5]:
                    tprint(f"     • Key('{k['key']}') for {k['widget_type']} — {k['purpose'][:50]}")
                dev._current_step = "dev_inject_keys"
                implementation = dev.inject_widget_keys(implementation, missing_keys)
                self._save("dev", implementation)
                self.results["dev"] = implementation

            # Dev fix per task TechLead  triage
            fixes = self._extract_fixes_required(review_output)
            fix_guide = fixes or blockers
            if tl_fix_assignment:
                fix_guide = [tl_fix_assignment] + fix_guide
            tprint(f"\n  🔄 Dev fixing per TechLead guidance...")
            dev._current_step = "dev_fix"
            implementation = dev.revise(implementation, fix_guide, product_idea)
            self._save("dev", implementation)
            self.results["dev"] = implementation
            self._step_token_status("Dev fix")

            # QA re-verifies
            tprint(f"\n  🔄 QA re-verifying after Dev fix...")
            qa._current_step = "test_review"
            review_output = qa.review_implementation(test_plan_doc, implementation, dev_clarification)
            self._save("test", review_output)
            self.results["test"] = review_output
            self._step_token_status("QA re-verify")

        return implementation

    @staticmethod
    def _extract_blockers(review_output: str) -> list[str]:
        """Parse BLOCKER lines from QA review output."""
        import re
        blockers = []
        for line in review_output.splitlines():
            if re.search(r"severity:\s*blocker", line, re.IGNORECASE):
                blockers.append(line.strip())
            elif "BLOCKER" in line and line.strip().startswith("TC-"):
                blockers.append(line.strip())
        return blockers

    @staticmethod
    def _extract_missing_widget_keys(review_output: str) -> list[dict]:
        """
        Parse QA review for missing widget keys.
        Detects lines like:
          - Missing key: Key('login_email') in TextField (lib/auth/login.dart) — email input
          - Need widget key: 'submit_btn' on ElevatedButton for login submit action
        Returns [{"key": "...", "widget_type": "...", "file_hint": "...", "purpose": "..."}]
        """
        import re
        missing: list[dict] = []
        # Primary pattern
        pat1 = re.compile(
            r"[Mm]issing\s+key[:\s]+[Kk]ey\(['\"]([^'\"]+)['\"]\)"
            r"(?:\s+in\s+(\w+))?"
            r"(?:\s*\(([^)]+)\))?"
            r"(?:\s*[—-]\s*(.+))?",
        )
        pat2 = re.compile(
            r"[Nn]eed\s+widget\s+key[:\s]+['\"]([^'\"]+)['\"]"
            r"(?:\s+on\s+(\w+))?"
            r"(?:\s+in\s+([^\s]+))?"
            r"(?:\s+for\s+(.+))?",
        )
        for line in review_output.splitlines():
            for pat in (pat1, pat2):
                m = pat.search(line)
                if m:
                    missing.append({
                        "key":         m.group(1).strip(),
                        "widget_type": (m.group(2) or "widget").strip(),
                        "file_hint":   (m.group(3) or "").strip(),
                        "purpose":     (m.group(4) or "").strip(),
                    })
                    break
        # Dedupe by key name
        seen = set()
        out = []
        for item in missing:
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            out.append(item)
        return out

    @staticmethod
    def _extract_fixes_required(review_output: str) -> list[str]:
        """Extract lines from the FIXES REQUIRED section."""
        lines = review_output.splitlines()
        collecting = False
        fixes = []
        for line in lines:
            stripped = line.strip()
            if "FIXES REQUIRED" in stripped.upper():
                collecting = True
                continue
            if collecting:
                if stripped.startswith("##"):
                    break
                if stripped and not stripped.startswith("#"):
                    fixes.append(stripped.lstrip("-•* "))
        return [f for f in fixes if f]

    def _save_implementation_files(self, dev_output: str):
        """Extract Dart code blocks from dev output and write them as real files in the project."""
        import re

        project_dir = self._resolve_project_dir()
        if not project_dir:
            tprint("  ℹ️  No có project path — code is lưu in dev.md, tạo file thủ công.")
            return

        # Match all ```dart ... ``` blocks
        blocks = re.findall(r"```dart\s*(.*?)```", dev_output, re.DOTALL)
        if not blocks:
            tprint("  ℹ️  No tìm thấy code block in dev output.")
            return

        saved = []
        for block in blocks:
            block = block.strip()
            if len(block) < 20:
                continue
            # Extract file path from first comment line: // lib/...dart
            m = re.match(r"//\s*(lib/[\w/.\-]+\.dart)", block)
            if not m:
                continue
            rel_path = m.group(1)
            file_path = project_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(block, encoding="utf-8")
            saved.append(rel_path)

        if saved:
            tprint(f"\n  💾 Done tạo {len(saved)} file(s) in project:")
            for p in saved:
                tprint(f"     ✅ {p}")
        else:
            tprint("  ⚠️  No tìm thấy path hợp lệ in code blocks — kiểm tra comment đầu mỗi block.")

    def _save_flutter_tests(self, review_output: str):
        """Extract Patrol Dart test + Maestro YAML flows from QA output, save, then auto-run."""
        import re
        self._last_patrol_result = None
        self._last_maestro_result = None

        # Patrol (Dart integration test)
        m_dart = re.search(r"```dart\s*(.*?)```", review_output, re.DOTALL)
        patrol_code = m_dart.group(1).strip() if m_dart else ""
        if patrol_code and len(patrol_code) > 50:
            dart_path = self.output_dir / f"{self.session_id}_patrol_test.dart"
            dart_path.write_text(patrol_code, encoding="utf-8")
            tprint(f"  🧪 Patrol test code → {dart_path.name}")

        # Maestro YAML flows — may have multiple blocks
        maestro_blocks: dict[str, str] = {}
        for i, m_yaml in enumerate(re.finditer(r"```ya?ml\s*(.*?)```", review_output, re.DOTALL), 1):
            content = m_yaml.group(1).strip()
            if "appId:" not in content:
                continue
            # Extract flow name from a header comment if present, else index
            name_m = re.search(r"#\s*maestro/([\w_/.\-]+\.yaml)", content)
            name = name_m.group(1).split("/")[-1] if name_m else f"flow_{i}.yaml"
            maestro_blocks[name] = content
        if maestro_blocks:
            yaml_dir = self.output_dir / f"{self.session_id}_maestro"
            yaml_dir.mkdir(parents=True, exist_ok=True)
            for name, content in maestro_blocks.items():
                (yaml_dir / name).write_text(content, encoding="utf-8")
            tprint(f"  🌊 Maestro flows ({len(maestro_blocks)}) → {yaml_dir.name}/")

        if not self.maintain_mode:
            tprint(f"  ℹ️  No có project path — chạy thủ công: patrol test + maestro test")
            return

        # Auto-run on real project
        if patrol_code:
            self._run_patrol_tests(
                patrol_code,
                dev_agent=self.agents.get("dev"),
                qa_agent=self.agents.get("test"),
            )
        if maestro_blocks:
            self._run_maestro_flows(maestro_blocks)

    def _run_maestro_flows(self, flows: dict[str, str]):
        """Install and run Maestro YAML flows."""
        from testing.maestro_runner import MaestroRunner
        project_dir = self._resolve_project_dir()
        if not project_dir:
            tprint(f"  ⚠️  No xác định is project dir — skip Maestro.")
            return
        runner = MaestroRunner(project_dir)
        if not runner.ensure_installed():
            return
        runner.install_flows(flows)
        result = runner.run_all()
        tprint(runner.format_suite_report(result))
        self._last_maestro_result = result

    def _run_patrol_tests(self, code: str, dev_agent=None, qa_agent=None):
        """
        Install Patrol test → run on Android + iOS → if fail: Dev fixes → re-run (max 2 rounds).
        """
        import re as _re
        from testing.patrol_runner import PatrolRunner

        project_dir = self._resolve_project_dir()
        if not project_dir:
            tprint(f"  ⚠️  Cannot determine project dir — skipping auto-run tests.")
            return

        tprint(f"\n  {'═'*60}")
        tprint(f"  🔨 PATROL AUTO-TEST — project: {project_dir}")
        tprint(f"  {'═'*60}")

        MAX_FIX_ROUNDS = 2
        for round_num in range(1, MAX_FIX_ROUNDS + 2):
            tprint(f"\n  ▶  Round {round_num} — chạy Patrol Android + iOS...")
            mr = runner.run_all_platforms(code)
            tprint(runner.format_multi_report(mr))
            self._last_patrol_result = mr

            # Save combined report
            combined = "\n\n".join(
                f"=== {r.platform.upper()} ({r.device_name}) ===\n{r.raw_output}"
                for r in [mr.android, mr.ios] if r
            )
            report_path = self.output_dir / f"{self.session_id}_test_run_{round_num}.txt"
            report_path.write_text(combined, encoding="utf-8")

            if mr.all_passed:
                tprint(f"\n  ✅ Android + iOS đều PASSED — sẵn sàng bàn giao.")
                break

            if round_num > MAX_FIX_ROUNDS or not dev_agent or not qa_agent:
                tprint(f"\n  ❌ Tests still FAIL after {MAX_FIX_ROUNDS} time fix — need Dev xem thủ công.")
                tprint(f"     Report: {report_path.name}")
                break

            # Dev fixes based on actual device failures
            all_failures = mr.all_failures()
            tprint(f"\n  🔄 Dev fix dựa trên result thực tế (round {round_num}/{MAX_FIX_ROUNDS})...")
            failure_context = (
                "Flutter tests FAIL trên device thực tế:\n"
                + "\n".join(f"  {f}" for f in all_failures[:10])
                + f"\n\nRaw output:\n{combined[-2000:]}"
            )
            dev_agent._current_step = "dev_test_fix"
            if not self._check_quota(f"Dev fix test failures round {round_num}"):
                break
            implementation = dev_agent.revise(
                self.results.get("dev", ""),
                [failure_context],
                self.results.get("ba", ""),
            )
            self.results["dev"] = implementation
            self._save("dev", implementation)

            # QA re-generates test code
            tprint(f"  🔄 QA update test code...")
            qa_agent._current_step = "test_review"
            test_plan_path = self._checkpoint_path("test_plan")
            test_plan_doc  = (test_plan_path.read_text(encoding="utf-8")
                              if test_plan_path.exists() else "")
            new_review = qa_agent.review_implementation(test_plan_doc, implementation)
            new_m = _re.search(r"```dart\s*(.*?)```", new_review, _re.DOTALL)
            if new_m and len(new_m.group(1).strip()) > 50:
                code = new_m.group(1).strip()

    def _resolve_project_dir(self) -> Path | None:
        """Find Flutter project directory from agents' project_context or cwd."""
        # Try agents that have project_context set
        for key in ["dev", "techlead", "ba"]:
            agent = self.agents.get(key)
            ctx = getattr(agent, "project_context", "") or ""
            if not ctx:
                continue
            # Look for absolute path with pubspec.yaml
            import re
            for match in re.finditer(r"(/[/\w.\-]+)", ctx[:2000]):
                candidate = Path(match.group(1))
                if (candidate / "pubspec.yaml").exists():
                    return candidate
        # Fallback: cwd if it has pubspec.yaml
        cwd = Path.cwd()
        if (cwd / "pubspec.yaml").exists():
            return cwd
        return None

    @staticmethod
    def _extract_missing_info(text: str) -> list[dict]:
        """Parse MISSING_INFO: [what] — MUST_ASK: [who] lines from agent output."""
        import re
        items = []
        for line in text.splitlines():
            m = re.match(r"MISSING_INFO:\s*(.+?)(?:\s*[—-]+\s*MUST_ASK:\s*(.+))?$", line.strip())
            if m:
                items.append({
                    "info": m.group(1).strip(),
                    "source": (m.group(2) or "User").strip(),
                })
        return items

    def _batch_clarify(
        self,
        outputs: dict[str, str],          # {agent_key: output_text}
        peer_agents: dict[str, object],
        original_task: str = "",
    ) -> dict[str, str]:
        """
        Hướng 3: Post-produce batch clarification.
        1. Scan all outputs for MISSING_INFO blocks
        2. Try to resolve each via peer agents first
        3. Collect whatever's still unresolved → ask user ONCE in batch
        4. Revise only agents that had unresolved MISSING_INFO
        Returns {agent_key: revised_output} for agents that were revised.
        """
        # Gather all missing info across all outputs
        all_items: list[dict] = []
        for key, text in outputs.items():
            for item in self._extract_missing_info(text):
                all_items.append({**item, "agent_key": key})

        if not all_items:
            return {}

        tprint(f"\n  {'═'*60}")
        tprint(f"  📋 POST-PRODUCE CLARIFICATION — {len(all_items)} info needed xác nhận")
        tprint(f"  {'═'*60}")

        # Step 1: Try peer agents first (no input() needed)
        source_map = {
            "BA": "ba", "TECHLEAD": "techlead", "TECH LEAD": "techlead",
            "DESIGN": "design",
        }
        resolved: dict[str, str] = {}

        for item in all_items:
            src_key = source_map.get(item["source"].upper())
            if src_key and src_key in peer_agents:
                peer = peer_agents[src_key]
                tprint(f"\n  ❓ [{item['agent_key']}] {item['info']}")
                tprint(f"     → Hỏi {peer.ROLE}...")
                answer = peer.respond_to("Orchestrator", item["info"])
                if answer and len(answer.strip()) > 10:
                    resolved[item["info"]] = answer
                    tprint(f"     ✅ {peer.ROLE}: {answer[:80]}...")
                    continue
            # Mark as unresolved for user batch
            resolved.setdefault(item["info"], None)

        # Step 2: Batch ask user for still-unresolved items (dedup by info text)
        seen_info: set[str] = set()
        unresolved = []
        for item in all_items:
            if resolved.get(item["info"]) is None and item["info"] not in seen_info:
                seen_info.add(item["info"])
                unresolved.append(item)
        if unresolved:
            tprint(f"\n  {'─'*60}")
            tprint(f"  💬 {len(unresolved)} question need user answer (1 time duy nhất):")
            tprint(f"  {'─'*60}")
            for i, item in enumerate(unresolved, 1):
                tprint(f"\n  {i}. [{item['agent_key'].upper()}] {item['info']}")
                tprint(f"     Nguồn đề xuất: {item['source']}")
                answer = input("     Answer: ").strip()
                if answer:
                    resolved[item["info"]] = answer
                else:
                    tprint("     ⚠️  Skip — agent will giữ placeholder.")

        # Step 3: Revise agents that had missing info
        actually_resolved = {k: v for k, v in resolved.items() if v}
        if not actually_resolved:
            return {}

        revised: dict[str, str] = {}
        for key, text in outputs.items():
            agent_items = [i for i in all_items if i["agent_key"] == key]
            applicable = {i["info"]: actually_resolved[i["info"]]
                         for i in agent_items if i["info"] in actually_resolved}
            if not applicable:
                continue
            agent = self.agents[key]
            tprint(f"\n  🔄 Revise {agent.ROLE} with {len(applicable)} thông tin  xác nhận...")
            # BA use revise_with_answers to tổng hợp sạch, phân cấp rõ
            if key == "ba" and hasattr(agent, "revise_with_answers"):
                revised[key] = agent.revise_with_answers(text, applicable, original_task)
            else:
                guide = [f'Tor thế placeholder "{info}" bằng: {ans}'
                         for info, ans in applicable.items()]
                revised[key] = agent.revise(text, guide, original_task)

        if revised:
            tprint(f"\n  ✅ Done revise: {', '.join(revised.keys())}")
        return revised

    def _resolve_info_needs(
        self,
        agent,
        task_description: str,
        available_context: str,
        peer_agents: dict[str, object],
        *,
        _called_from_thread: bool = False,
    ) -> dict:
        """
        Pre-produce check (Phương án 2):
        1. Ask agent what it needs
        2. Route each NEED to peer agent (message bus) or user
        3. Return resolved {need: answer} dict to inject into produce call
        """
        needs = agent.plan_needed_info(task_description, available_context)
        if not needs:
            return {}

        tprint(f"\n  {'─'*60}")
        tprint(f"  📋 PRE-PRODUCE CHECK — {agent.ROLE} need {len(needs)} thông tin")
        tprint(f"  {'─'*60}")

        resolved: dict[str, str] = {}
        source_map = {
            "BA": "ba", "TECHLEAD": "techlead", "TECH LEAD": "techlead",
            "DESIGN": "design",
        }

        for item in needs:
            need = item["need"]
            source_key = source_map.get(item["source"], None)
            tprint(f"\n  ❓ Need biết: {need}")
            tprint(f"     Nguồn: {item['source']}")

            answer = None

            # Try peer agent first
            if source_key and source_key in peer_agents:
                peer = peer_agents[source_key]
                tprint(f"     → Hỏi {peer.ROLE}...")
                answer = agent.ask(peer, need)
                if answer and len(answer.strip()) > 10:
                    resolved[need] = answer
                    tprint(f"     ✅ Done có answer from {peer.ROLE}")
                    continue

            # Fallback: ask user — only safe outside parallel threads
            if _called_from_thread:
                tprint(f"     ⚠️  Cannot hỏi user from parallel thread — agent will flag MISSING_INFO.")
                continue
            tprint(f"     → Peer agent no enough thông tin. Hỏi user:")
            tprint(f"     {need}")
            user_ans = input(f"     Answer: ").strip()
            if user_ans:
                resolved[need] = user_ans
                tprint(f"     ✅ Done ghi nhận answer from user.")
            else:
                tprint(f"     ⚠️  No có answer — agent will flag MISSING_INFO in output.")

        tprint(f"\n  ✅ Resolved {len(resolved)}/{len(needs)} — proceed produce.\n")
        return resolved
        # Project info (maintain mode)
        if self.project_info:
            lines.append(f"**Project:** {self.project_info.name} ({self.project_info.kind})\n")
            lines.append(f"**Root:** `{self.project_info.root}`\n\n")

        lines.append("## Artifacts\n")
        for key, role, _ in self.PIPELINE:
            lines.append(f"- [{role}]({self._checkpoint_path(key).name})\n")

        # Git diff (maintain mode)
        if self.git_helper and self.git_snapshot:
            diff = self.git_helper.diff_since(self.git_snapshot)
            lines.append(f"\n## Code Changes\n")
            lines.append(f"- Files changed: **{diff.files_changed}**\n")
            lines.append(f"- Insertions: **+{diff.insertions}**  Deletions: **-{diff.deletions}**\n")
            lines.append(f"- Branch: `{self.git_snapshot.created_branch or self.git_snapshot.branch}`\n\n")
            if diff.files:
                lines.append("Files:\n")
                for f in diff.files[:30]:
                    lines.append(f"  - {f}\n")
            tprint("\n" + self.git_helper.format_diff(diff))
            self._last_diff = diff

        # Health check delta (post-pipeline)
        if self.project_info and self.health_report:
            try:
                from context import HealthChecker
                after = HealthChecker(self.project_info, timeout_s=60).run(skip_tests=True)
                new_errors = max(0, after.analyze_errors - self.health_report.analyze_errors)
                lines.append(f"\n## Health Delta\n")
                lines.append(f"- Analyze errors: {self.health_report.analyze_errors} → "
                             f"{after.analyze_errors}  ({'+' if new_errors else ''}{new_errors} new)\n")
                if new_errors:
                    tprint(f"  ⚠️  Pipeline introduced {new_errors} new analyze errors")
                    lines.append(f"  ⚠️ Pipeline  tạo ra {new_errors} bug analyze new\n")
                else:
                    tprint(f"  ✅ No tạo thêm analyze error nào")
                self._last_health_after = after
            except Exception as e:
                lines.append(f"\n## Health Delta\n- (check failed: {e})\n")

        lines.append(f"\n## Conversations\n")
        lines.append(f"- [Agent Conversations]({self.session_id}_conversations.md) "
                     f"({len(self.bus.log)} exchanges)\n")

        # Use new naming if in per-project layout
        summary_path = self._checkpoint_path("SUMMARY")
        summary_path.write_text("".join(lines), encoding="utf-8")
        tprint(f"\n  📄 Summary → {summary_path.name}")

    # ── Auto-feedback + HTML report ───────────────────────────────────────────

    def _collect_auto_feedback(self):
        """Build app + run Maestro + scrape logcat + diff screenshots → auto-trigger feedback mode."""
        self._last_feedback_report = None
        project_dir = self._resolve_project_dir()
        if not project_dir:
            tprint("  ℹ️  No project path — skipping auto-feedback.")
            return

        # Skip if user already set env var to opt out
        if os.environ.get("MULTI_AGENT_NO_AUTO_FEEDBACK", "").strip() == "1":
            tprint("  ℹ️  MULTI_AGENT_NO_AUTO_FEEDBACK=1 — skip auto-feedback.")
            return

        try:
            from testing.auto_feedback import AutoFeedback
        except ImportError:
            return

        tprint(f"\n  {'═'*60}")
        tprint(f"  💬 AUTO-FEEDBACK — build app + run E2E + scrape logs")
        tprint(f"  {'═'*60}")

        fb = AutoFeedback(project_dir)
        design_agent = self.agents.get("design")
        design_specs = self.results.get("design", "")

        def _vision_call(system, user, image_path):
            return design_agent._call_with_image(system, user, image_path)

        report = fb.collect(design_specs, _vision_call, platform="android")
        fb.print_report(report)
        self._last_feedback_report = report

        # Auto-trigger feedback pipeline if blockers found
        if report.has_blockers and os.environ.get("MULTI_AGENT_AUTO_HEAL", "1") == "1":
            tprint("\n  🔁 BLOCKER detected — running feedback mode to auto self-heal...")
            feedback_payload = report.to_feedback_dict()
            if feedback_payload:
                try:
                    # Use existing feedback pipeline but skip interactive prompts
                    self._auto_run_feedback(feedback_payload)
                except Exception as e:
                    tprint(f"  ⚠️  Auto-heal failed: {e}")

    def _auto_run_feedback(self, feedback: dict):
        """Non-interactive version of run_feedback — used by auto self-heal."""
        existing: dict[str, str] = {}
        for key in self.STEP_KEYS:
            if key in self.results:
                existing[key] = self.results[key]

        if not existing:
            return

        ba = self.agents["ba"]
        ba._current_step = "auto_feedback_assessment"
        assessment = ba.assess_feedback(
            feedback["description"], existing, feedback["type"]
        )
        tprint(f"  🎯 Auto-heal will re-run: {', '.join(assessment['affected'])}")

        # Load affected checkpoints marker — force re-run
        for key in assessment["affected"]:
            self.results.pop(key, None)
            cp = self._checkpoint_path(key)
            if cp.exists():
                cp.rename(cp.with_suffix(".md.before_autoheal"))

        # Re-run the pipeline with results already set for unchanged steps
        task = f"[AUTO-HEAL] {feedback['description'][:300]}"
        self._run_task_based_pipeline(task)

    def _write_html_report(self):
        """Generate HTML dashboard at outputs/<project>/<session>_REPORT.html"""
        try:
            from reporting.html_report import build_report
        except ImportError as e:
            tprint(f"  ⚠️  html_report unavailable: {e}")
            return

        skill_usage: list[dict] = []
        for agent in list(self.agents.values()) + [self.critic, self.investigator]:
            skill_usage.extend(getattr(agent, "_skill_usage_log", []) or [])

        # Token summary
        by_agent: dict[str, int] = {}
        for rec in self.tokens.records:
            by_agent[rec.agent] = by_agent.get(rec.agent, 0) + rec.total
        token_summary = {
            "budget":   self.tokens.budget,
            "used":     self.tokens.used,
            "pct":      self.tokens.pct,
            "by_agent": by_agent,
        }

        out_path = build_report(
            session_id=self.session_id,
            project_name=self.project_name,
            profile=self.profile,
            critic_reviews=self.critic_reviews,
            skill_usage=skill_usage,
            token_summary=token_summary,
            patrol_result=getattr(self, "_last_patrol_result", None),
            maestro_result=getattr(self, "_last_maestro_result", None),
            feedback_report=getattr(self, "_last_feedback_report", None),
            pipeline_steps=self.STEP_KEYS,
            out_dir=self.output_dir,
        )
        tprint(f"\n  📊 HTML Report → {out_path.name}")
        tprint(f"     Mở bằng: open {out_path}")
