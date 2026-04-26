"""Multi-Agent Orchestrator with real agent-to-agent communication + parallel execution."""
from pathlib import Path

from core.message_bus import MessageBus
from core.logging import tprint          # canonical thread-safe print
from core.config import get_bool
from agents import BAAgent, DesignAgent, TechLeadAgent, DevAgent, TestAgent, CriticAgent, RuleOptimizerAgent, InvestigationAgent, SkillDesignerAgent, PMAgent
from agents.pm_agent import RouteDecision
from core.token_tracker import TokenTracker


# ── Context management ────────────────────────────────────────────────────────
# Text helpers moved to core/text_utils.py — re-exported here for backward
# compatibility so internal callers `_smart_trim(...)` keep working.


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
        from session import SessionManager

        project_name = detect_project_name(maintain_dir or Path.cwd())

        # Prefer in-project output (.multi_agent/sessions/) when maintain_dir given
        fallback = Path(output_dir) / project_name
        resolved_output = resolve_output_dir(maintain_dir, fallback)

        # SessionManager owns session_id, output_dir, checkpoints, results.
        # Orchestrator exposes `session_id`, `output_dir`, `project_name`,
        # `results` as pass-through properties for backward compatibility.
        self.session_mgr = SessionManager(
            output_dir=output_dir,
            project_name=project_name,
            resolved_output_dir=resolved_output,
            resume_session=resume_session,
            step_keys=self.STEP_KEYS,
        )
        if resume_session:
            self.session_mgr.load_checkpoints()

        self.project_info = None          # populated by _init_project_info later
        self.git_helper   = None          # GitHelper instance when in maintain mode
        self.git_snapshot = None          # baseline snapshot before pipeline runs
        self.health_report = None         # HealthReport pre-flight

        self.bus = MessageBus()
        self.profile = profile

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
            # Wire SkillDesigner so any agent can request a fresh skill on
            # the fly when no existing one matches the user's task. Don't
            # wire it onto SkillDesigner itself (it'd just call itself).
            if agent is not self.skill_designer:
                agent._skill_designer = self.skill_designer

        # ── Maintain mode: inject existing project context into agents ────────
        if maintain_dir:
            self._load_project_context(maintain_dir)
        elif not maintain_dir:
            self._auto_detect_maintain()

    # ── SessionManager delegation (back-compat for existing callers) ──────────

    @property
    def session_id(self) -> str:
        return self.session_mgr.session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self.session_mgr.session_id = value

    @property
    def output_dir(self) -> Path:
        return self.session_mgr.output_dir

    @output_dir.setter
    def output_dir(self, value) -> None:
        self.session_mgr.output_dir = Path(value)

    @property
    def project_name(self) -> str:
        return self.session_mgr.project_name

    @project_name.setter
    def project_name(self, value: str) -> None:
        self.session_mgr.project_name = value

    @property
    def _output_dir_base(self) -> str:
        return self.session_mgr._output_dir_base

    @property
    def results(self) -> dict[str, str]:
        return self.session_mgr.results

    @results.setter
    def results(self, value: dict[str, str]) -> None:
        self.session_mgr.results = value

    # ── Maintain mode ─────────────────────────────────────────────────────────

    def _auto_detect_maintain(self):
        from context.maintain_detector import auto_detect_maintain
        return auto_detect_maintain(self)

    def _detect_maintain_from_task(self, task: str):
        from context.maintain_detector import detect_maintain_from_task
        return detect_maintain_from_task(self, task)

    def _load_project_context(self, maintain_dir: str, task_hint: str = ''):
        from context.maintain_detector import load_project_context
        return load_project_context(self, maintain_dir, task_hint)

    # ── Checkpoint helpers ────────────────────────────────────────────────────

    # Session I/O — delegates to SessionManager for the actual work.
    def _checkpoint_path(self, key: str) -> Path:
        return self.session_mgr.checkpoint_path(key)

    def _load_checkpoints(self) -> None:
        self.session_mgr.load_checkpoints()

    def _step_done(self, key: str) -> bool:
        return self.session_mgr.is_step_done(key)

    @classmethod
    def list_sessions(cls, output_dir: str = "outputs", project_name: str | None = None) -> list[dict]:
        """Return resumable sessions. Searches within project subdir if project_name given, else all projects."""
        from session import SessionManager
        return SessionManager.list_sessions(output_dir, project_name, cls.STEP_KEYS)

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
                if choice in ("C", "S", ""):
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
        """Persist one step output. Handles the Dev auto-commit side-effect here
        (SessionManager stays focused on plain file I/O)."""
        self.session_mgr.save(key, content)

        # Optional: auto-commit code-producing step (dev) to the multi-agent branch
        if key == "dev" and self.git_helper and self.git_helper.is_repo():
            if get_bool("MULTI_AGENT_AUTO_COMMIT"):
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
                if getattr(agent, "_active_skills", None):
                    return  # already detected — skip to avoid duplicate
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
            except (AttributeError, KeyError, ValueError, TypeError):
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
                except (AttributeError, KeyError, ValueError):
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
        from pipeline.critic_gating import apply_dynamic_weights
        return apply_dynamic_weights(self, review, agent)

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
        from pipeline.critic_gating import techlead_touches_core
        return techlead_touches_core(self, tl_output)

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
        from pipeline.critic_gating import get_audit_log
        return get_audit_log(self)

    def _record_critic_skip(self, key: str, tasks: list) -> None:
        from pipeline.critic_gating import record_critic_skip
        return record_critic_skip(self, key, tasks)

    def _trigger_emergency_audit(self, blockers: list[str], tasks: list, agent_in_charge: str = 'Dev') -> None:
        from pipeline.critic_gating import trigger_emergency_audit
        return trigger_emergency_audit(self, blockers, tasks, agent_in_charge)

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
        from pipeline.pm_router import apply_pm_metadata
        return apply_pm_metadata(self, route, tasks)

    def _fast_track_announce(self, key: str, context: dict) -> None:
        from pipeline.critic_gating import fast_track_announce
        return fast_track_announce(self, key, context)

    def _critic_enabled_for(self, key: str, output: str = '', context: dict | None = None) -> bool:
        from pipeline.critic_gating import critic_enabled_for
        return critic_enabled_for(self, key, output, context)

    # Critic loop methods → delegate to pipeline/critic_loop.py
    def _review_only(self, key: str, agent, output: str,
                      original_prompt: str = "", context: dict | None = None) -> str:
        from pipeline.critic_loop import review_only as _do
        return _do(self, key, agent, output, original_prompt, context)

    def _run_with_review(self, key: str, agent, produce_fn,
                          original_prompt: str = "") -> str:
        from pipeline.critic_loop import run_with_review as _do
        return _do(self, key, agent, produce_fn, original_prompt)

    def _escalate(self, key: str, agent, output: str,
                   review: dict, original_prompt: str) -> str:
        from pipeline.critic_loop import escalate as _do
        return _do(self, key, agent, output, review, original_prompt)

    def _save_conversations(self):
        from session.conversation_export import save_conversations
        return save_conversations(self)

    def _serialize_critic_reviews(self) -> list[dict]:
        from session.conversation_export import serialize_critic_reviews
        return serialize_critic_reviews(self)

    def _serialize_skills_used(self) -> dict:
        from session.conversation_export import serialize_skills_used
        return serialize_skills_used(self)

    # ── Main pipeline ─────────────────────────────────────────────────────────

    # ── PM router (Step 0) ────────────────────────────────────────────────────

    def _run_pm_router(self, product_idea: str):
        from pipeline.pm_router import run_pm_router
        return run_pm_router(self, product_idea)

    def _pm_clarify_with_user(self, decision: 'RouteDecision', product_idea: str) -> 'RouteDecision':
        from pipeline.clarification import pm_clarify_with_user
        return pm_clarify_with_user(self, decision, product_idea)

    def _run_investigation_path(self, product_idea: str) -> dict:
        from pipeline.pm_router import run_investigation_path
        return run_investigation_path(self, product_idea)

    # ── Task-based pipeline (NEW FLOW) ────────────────────────────────────────

    def _run_task_based_pipeline(self, product_idea: str,
                                  resources: dict | None = None,
                                  allowed_steps: list[str] | None = None,
                                  pm_route: "RouteDecision | None" = None) -> dict:
        from pipeline.task_based_runner import run_task_based_pipeline
        return run_task_based_pipeline(self, product_idea, resources, allowed_steps, pm_route)

    def _find_existing_design_system(self) -> str:
        from context.maintain_detector import find_existing_design_system
        return find_existing_design_system(self)
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

        # Save original prompt so resume can show it
        try:
            prompt_path = self._checkpoint_path("prompt").with_suffix(".txt")
            prompt_path.write_text(product_idea, encoding="utf-8")
        except (OSError, UnicodeEncodeError):
            pass

        if not self.maintain_mode:
            self._detect_maintain_from_task(product_idea)

        # ── Activate rule A/B variants (shadow vs baseline) if any are live
        self._activate_rule_variants()

        # ── PM router runs BEFORE BA clarification so Investigation kind
        #    can skip the heavy clarification gate.
        route = self._run_pm_router(product_idea)

        # User declined the PM plan in the confirmation gate — stop early.
        if route.kind == "aborted":
            tprint(f"\n{'⏹'*5}  PIPELINE ABORTED BY USER  {'⏹'*5}")
            tprint(self.tokens.full_report())
            return self.results

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
        self._log_shadow_rule_scores()
        self._run_rule_optimizer()
        self._run_skill_optimizer()
        return self.results

    def _apply_outcome_adjustments(self, task: str):
        """Thin delegate — real work lives in analyzer.outcome_pipeline."""
        if not self.critic_reviews:
            return
        try:
            from analyzer import analyze_session
        except ImportError:
            return
        analyze_session(
            profile=self.profile,
            session_id=self.session_id,
            task=task,
            critic_reviews=self.critic_reviews,
            bus=self.bus,
            tokens=self.tokens,
            agents=self.agents,
            current_tasks=getattr(self, "_current_tasks", None),
            patrol_result=getattr(self, "_last_patrol_result", None),
            maestro_result=getattr(self, "_last_maestro_result", None),
        )

    def _print_score_breakdown(self, adjuster) -> None:
        """Render per-agent "receipt" — delegates to analyzer.score_renderer."""
        from analyzer.score_renderer import print_score_breakdown
        print_score_breakdown(
            critic_reviews=self.critic_reviews,
            adjustments=getattr(adjuster, "adjustments", None),
            tprint=tprint,
        )

    def _clarification_gate(self, product_idea: str) -> str:
        from pipeline.clarification import clarification_gate
        return clarification_gate(self, product_idea)

    def run_update(self, task: str, source_session: str) -> dict:
        from pipeline.session_runner import run_update
        return run_update(self, task, source_session)

    def run_feedback(self, source_session: str, feedback: dict) -> dict:
        from pipeline.session_runner import run_feedback
        return run_feedback(self, source_session, feedback)

    def run_resume(self) -> dict:
        from pipeline.session_runner import run_resume
        return run_resume(self)

    # ── Utilities ─────────────────────────────────────────────────────────────

    # Minimum total sessions before we trust shadow A/B. Rationale: with
    # solo-dev cadence, shadow verdict with <30 sessions per variant is
    # noise. Until threshold reached, always serve baseline.
    SHADOW_AB_MIN_TOTAL_SESSIONS = 30

    def _activate_rule_variants(self):
        from learning.shadow_runner import activate_rule_variants
        return activate_rule_variants(self)

    def _log_shadow_rule_scores(self):
        from learning.shadow_runner import log_shadow_rule_scores
        return log_shadow_rule_scores(self)

    # Per-(agent, scope, complexity) token budgets + trend thresholds live in
    # analyzer.cost_history now. Re-exported as class attrs so any external
    # consumer that read `orchestrator._COST_FALLBACK` etc. keeps working.
    from analyzer.cost_history import (
        COST_BUDGETS_DEFAULT as _COST_BUDGETS_DEFAULT,
        COST_FALLBACK         as _COST_FALLBACK,
        COST_RATIO_OVER_BUDGET as _COST_RATIO_OVER_BUDGET,
        COST_TREND_WINDOW      as _COST_TREND_WINDOW,
        COST_TREND_REQUIRED_OVER as _COST_TREND_REQUIRED_OVER,
    )

    def _load_cost_history(self) -> dict:
        from analyzer.cost_history import load_cost_history
        return load_cost_history(self.profile)

    def _save_cost_history(self, history: dict) -> None:
        from analyzer.cost_history import save_cost_history
        save_cost_history(self.profile, history)

    def _load_cost_budgets(self) -> dict:
        from analyzer.cost_history import load_cost_budgets
        return load_cost_budgets(self.profile)

    def _expected_budget_for_tasks(self, tasks: list, budgets: dict) -> dict:
        from analyzer.cost_history import expected_budget_for_tasks
        return expected_budget_for_tasks(tasks, budgets)

    def _build_cost_suggestions(self) -> list:
        from learning.runners import build_cost_suggestions
        return build_cost_suggestions(self)

    def _run_rule_evolver(self, raw_suggestions: list[dict], history):
        from learning.runners import run_rule_evolver
        run_rule_evolver(self, raw_suggestions, history)

    def _rule_path_for(self, agent_key: str, target_type: str):
        from learning.runners import rule_path_for
        return rule_path_for(self.profile, agent_key, target_type)

    def _run_rule_optimizer(self):
        from learning.runners import run_rule_optimizer
        run_rule_optimizer(self)

        # (Legacy second-pass prompt removed — the classifier-gated loop
        # above already routes every suggestion through user review when
        # mode=propose, or auto-applies when mode=auto and P(regress) is low.)

    def _run_skill_optimizer(self):
        """Delegate to learning.runners.run_skill_optimizer."""
        from learning.runners import run_skill_optimizer
        run_skill_optimizer(self)

    # ── Shadow A/B judge ──────────────────────────────────────────────────────

    def _judge_shadow_skills(self, optimizer):
        from learning.runners import judge_shadow_skills
        judge_shadow_skills(self, optimizer)

    # ── REFINE ────────────────────────────────────────────────────────────────

    def _apply_refine(self, optimizer, suggestion: dict):
        from learning.runners import apply_refine
        apply_refine(self, optimizer, suggestion)

    # ── CREATE ────────────────────────────────────────────────────────────────

    def _apply_create(self, optimizer, suggestion: dict):
        from learning.runners import apply_create
        apply_create(self, optimizer, suggestion)

    # ── MERGE ─────────────────────────────────────────────────────────────────

    def _apply_merge(self, optimizer, suggestion: dict):
        from learning.runners import apply_merge
        apply_merge(self, optimizer, suggestion)

    def _print_score_trends(self, history):
        from learning.runners import print_score_trends
        print_score_trends(self, history)

    # ── QA → TechLead → Dev fix loop ─────────────────────────────────────────

    def _option_c_spec_postmortem(self, tl, ba, dev,
                                    blockers: list[str], tasks: list,
                                    implementation: str, product_idea: str) -> bool:
        from pipeline.task_based_runner import option_c_spec_postmortem
        return option_c_spec_postmortem(self, tl, ba, dev, blockers, tasks, implementation, product_idea)

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
        from pipeline.task_based_runner import qa_dev_loop
        return qa_dev_loop(self, qa, dev, tl, test_plan_doc, implementation, dev_clarification, initial_review, product_idea, tasks)

    # Parsing helpers moved to pipeline.parsers — thin wrappers keep the
    # original staticmethod API so nothing else in the codebase breaks.
    @staticmethod
    def _extract_blockers(review_output: str) -> list[str]:
        from pipeline.parsers import extract_blockers
        return extract_blockers(review_output)

    @staticmethod
    def _extract_missing_widget_keys(review_output: str) -> list[dict]:
        from pipeline.parsers import extract_missing_widget_keys
        return extract_missing_widget_keys(review_output)

    @staticmethod
    def _extract_fixes_required(review_output: str) -> list[str]:
        from pipeline.parsers import extract_fixes_required
        return extract_fixes_required(review_output)

    def _save_implementation_files(self, dev_output: str):
        from testing.runners import save_implementation_files
        return save_implementation_files(self, dev_output)

    def _save_flutter_tests(self, review_output: str):
        from testing.runners import save_flutter_tests
        return save_flutter_tests(self, review_output)

    def _run_maestro_flows(self, flows: dict[str, str]):
        from testing.runners import run_maestro_flows
        return run_maestro_flows(self, flows)

    def _run_patrol_tests(self, code: str, dev_agent=None, qa_agent=None):
        from testing.runners import run_patrol_tests
        return run_patrol_tests(self, code, dev_agent, qa_agent)

    def _resolve_project_dir(self) -> Path | None:
        from testing.runners import resolve_project_dir
        return resolve_project_dir(self)

    @staticmethod
    def _extract_missing_info(text: str) -> list[dict]:
        from pipeline.parsers import extract_missing_info
        return extract_missing_info(text)

    def _batch_clarify(self, outputs: dict[str, str], peer_agents: dict[str, object], original_task: str = '') -> dict[str, str]:
        from pipeline.clarification import batch_clarify
        return batch_clarify(self, outputs, peer_agents, original_task)

    def _resolve_info_needs(self, key, agent, produce_fn, needs, peer_agents, original_task):
        from pipeline.clarification import resolve_info_needs
        return resolve_info_needs(self, key, agent, produce_fn, needs, peer_agents, original_task)

    # ── Auto-feedback + HTML report ───────────────────────────────────────────

    def _collect_auto_feedback(self):
        from analyzer.session_report import collect_auto_feedback
        return collect_auto_feedback(self)

    def _auto_run_feedback(self, feedback: dict):
        from analyzer.session_report import auto_run_feedback
        return auto_run_feedback(self, feedback)

    def _write_html_report(self):
        from analyzer.session_report import write_html_report
        return write_html_report(self)
