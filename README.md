# Multi-Agent Product Development Pipeline

A task-based AI pipeline that turns a product idea or bug report into classified tasks, prioritizes them by resource/business value, implements them, and verifies the output — all via specialized Claude agents with self-improving rules and skills.

## Flow

```
Input (task / feature / bug / URL)
    ↓
PM (route) → kind = feature | bug_fix | ui_tweak | refactor | investigation
    │             + confidence + reason; sub-pipeline selected per kind
    ↓
BA (classify)   → tasks with type={ui,logic,bug,hotfix,mixed}, priority, complexity, risk, business value
    ↓
Design (reuse-first) → UI tasks routed here; check existing DS → reuse or create
    ↓
BA (consolidate) → merge design refs back into task list
    ↓
TechLead (prioritize) → validate estimates, pack into sprints per team capacity
    ↓
Dev ∥ Test (parallel) → implementation + test plan in priority order
    ↓
QA → Dev fix loop → Patrol + Maestro auto-run
    ↓
Auto-feedback → build app, E2E, screenshot diff, logcat → self-heal if BLOCKERs
    ↓
HTML dashboard + meta-learning (rules/skills tune themselves)
```

### Sub-pipeline per PM `kind`

| kind            | Steps run                                                   |
|-----------------|-------------------------------------------------------------|
| `feature`       | BA → Design → TechLead → test_plan → Dev → Test (full)      |
| `bug_fix`       | BA(lite) → Dev → Test                                       |
| `ui_tweak`      | BA → Design → Dev → Test (skips TechLead + test_plan)       |
| `refactor`      | BA → TechLead → Dev → Test (skips Design)                   |
| `investigation` | Code Investigator only — direct answer, no pipeline         |

PM uses a heuristic keyword pass first; only falls back to an LLM call when the signal is mixed. When confidence `< 0.6` the CLI asks the user to confirm the kind before dispatching.

## Install

```bash
git clone https://github.com/TranNam2000/mutli_agent
cd mutli_agent
pip install -e .
```

Requires Python ≥ 3.10, `claude` CLI, optional: `patrol`, `maestro`, `flutter`, `git`, `adb`, `rg`.

Run `mag --doctor` to check all dependencies.

## Use

```bash
# From any project folder
cd ~/my_flutter_app
mag "add Google OAuth login"

# With resources
mag "build MVP video downloader" --dev-slots 3 --sprint-hours 100

# Resume a paused session
mag --list
mag --resume 20260419_180000_a3b2

# Cross-session trend dashboard
mag --trend
```

## Key features

- **Task classification** — BA outputs structured tasks with type/priority/complexity/risk/business-value
- **Reuse-first design** — Design checks existing design system before creating new specs
- **Resource-aware scheduling** — TechLead validates BA estimates, bin-packs tasks into sprints
- **Parallel Dev + Test** — test plan in priority order: P0 full coverage, P3 smoke only
- **Auto-feedback loop** — Patrol + Maestro + logcat scraper + vision-diff vs design specs → self-heal BLOCKERs
- **Maintain mode** — auto-detect project, scoped keyword-driven context, git branch per session
- **Meta-learning** — rules and skills tune themselves over sessions: auto-apply, shadow A/B, criteria upgrade, regression rollback
- **Skill system** — 20+ specialized skills per agent × scope (simple → feature → full_app → bug_fix), stack-specific (Flutter / React Native / Next.js)
- **HTML dashboard** — self-contained report with score trends, skill heatmap, Maestro thumbnails, feedback items

## Architecture

```
multi_agent/
├── main.py                  # CLI entry
├── orchestrator.py          # pipeline core
├── agents/                  # BA, Design, TechLead, Dev, QA, Critic, Investigation, RuleOptimizer, SkillDesigner
├── core/                    # message_bus, token_tracker, plan_detector, doctor
├── context/                 # project_detector, scoped_reader, git_helper, health_check, output_paths, refresh
├── learning/                # skill_selector, skill_optimizer, revise_history, score_adjuster, task_models
├── testing/                 # patrol_runner, maestro_runner, stitch_browser, auto_feedback
├── reporting/               # html_report, trend_report
├── rules/                   # system prompts + criteria per agent
└── skills/                  # specialized skill files per agent × scope
```

## Scoring mechanism

Every agent output goes through 5 layers of evaluation that feed into
self-improving rules and skills.

### 1. Critic checklist (per-step)

Flow on default settings (with structured Task metadata driving the gate):

```
Fast-Track                               Emergency Audit (if QA fails)
1. PM / BA   → Critic SKIPPED            1. QA flags BLOCKER + skip happened upstream
2. TechLead  → Critic CONDITIONAL   ─►   2. RCA entry → audit_log.jsonl
3. Dev       → Critic MANDATORY          3. Critic forced ON for the rest of the session
4. Test      → Critic MANDATORY          4. IntegrityRules mutate (module blacklist,
                                             keyword risk, agent reputation)
                                         5. integrity.md is regenerated
```

This cuts ~30–40% of Critic LLM calls per session on average while guaranteeing
that any pattern that ever caused a false-negative gets Critic forced on in
future sessions.

**TechLead Critic trigger table** (priority order):

| Condition                                                     | Critic |
|---------------------------------------------------------------|--------|
| Env `MULTI_AGENT_TL_CRITIC_ALWAYS=1`                          | RUN    |
| Env `MULTI_AGENT_TL_CRITIC_NEVER=1`                           | SKIP   |
| Any task has `complexity=L` or `XL`                           | RUN    |
| Any task has `type=bug` or `hotfix`                           | RUN    |
| All tasks have `complexity` ∈ {S, M} on non-bug types         | SKIP   |
| (fallback) Output mentions core files (main.dart, router, DI) | RUN    |
| otherwise                                                     | SKIP   |

Core-file detection matches: `main.dart`, `app_router`, `service_locator.dart`,
`injection_container.dart`, `BaseRepository/BaseBloc/...`, `core/(router|di|network|...)`, `dependency_injection`.

Set `MULTI_AGENT_CRITIC_ALL=1` to run Critic for every step (legacy behaviour).

After a Critic-enabled agent produces an output, the Critic grades it against
the skill's criteria file using **3 levels** (not binary YES/NO):

| Grade       | Value | Meaning                                                     |
|-------------|-------|-------------------------------------------------------------|
| **FULL**    | 1.0   | Fully done, good quality                                    |
| **PARTIAL** | 0.5   | Done but incomplete / shallow (≥ 50% but < 100%)            |
| **MISS**    | 0.0   | Fully missing or wrong                                      |

Each criteria file (`rules/<profile>/criteria/<agent>.md`) defines:

```
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.40 format=0.20 quality=0.40

## Completeness (what must exist)
- [ ] item 1
- [ ] item 2

## Format (structure rules)
- [ ] item A

## Quality (depth / usefulness)
- [ ] item X
```

**Score calculation:**

```
dim_score = sum(grades) / total_items × 10        # per dimension
final     = floor(c × Wc + f × Wf + q × Wq)
verdict   = PASS if final ≥ PASS_THRESHOLD else REVISE
```

**Auto-penalties (override scoring):**
- `MISSING_INFO` still in output → quality capped at 4
- Output < 200 chars → quality capped at 3

**Revise loop:** if REVISE, the agent gets `revision_guide` and retries up to
2 rounds. After 2 failed rounds → escalation prompt [Continue / Retry / Skip].

### 2. Task priority score (during planning)

In the task-based flow, BA produces tasks that are scored for scheduling:

```
priority_score = priority_weight × business_value_boost × risk_multiplier / √hours
```

| Field            | Values & multiplier                                          |
|------------------|--------------------------------------------------------------|
| priority         | P0=10, P1=6, P2=3, P3=1                                     |
| business_value   | critical=1.8, high=1.3, normal=1.0, low=0.6                 |
| risk             | low=1.0, med=1.3, high=1.7                                  |
| complexity hours | S=3, M=8, L=18, XL=40                                       |

Higher score → earlier in sprint. Dependencies enforced via topological order.

### 3. Score adjuster (post-pipeline reality check)

Critic score can be gamed by checklist-satisfying output. The `ScoreAdjuster`
blends critic score with real outcomes:

- Patrol / Maestro test fails → penalty on Dev / Design
- Downstream agent asks many clarifications → penalty on upstream
- `MISSING_INFO` leaks into next step → penalty on the producer
- Token usage wildly exceeds expected → penalty on all agents

**Dynamic weights per scope:**

| Scope      | completeness | format | quality |
|------------|--------------|--------|---------|
| simple     | 0.30         | 0.30   | 0.40    |
| bug_fix    | 0.25         | 0.15   | 0.60    |
| feature    | 0.35         | 0.20   | 0.45    |
| module     | 0.40         | 0.20   | 0.40    |
| full_app   | 0.45         | 0.15   | 0.40    |

### 4. Meta-learning triggers (cross-session)

After each pipeline run:

| Trigger                                           | Action                                  |
|---------------------------------------------------|-----------------------------------------|
| REVISE pattern recurs ≥ 5 sessions                | Auto-apply rule/criteria change        |
| Applied rule causes score drop ≥ 0.5 (2+ sessions)| Auto-rollback + blacklist pattern      |
| Agent avg ≥ 8.5 for 3 consecutive sessions        | Auto-upgrade PASS_THRESHOLD +1         |
| Skill avg < 5.0 across ≥ 5 uses                   | Auto-deprecate (if other skills exist) |
| Skill stuck in 5-7 band across 4+ uses            | Auto-refine via shadow A/B             |
| Chronic misfit pattern ≥ 4 sessions               | Auto-create new shadow skill           |
| Two skills with ≥ 70% trigger overlap             | Auto-merge candidate                   |

### 5. Shadow A/B for skill evolution

New or refined skills don't replace parents immediately:

```
Shadow skill created → used for ≥ 2 sessions in parallel with parent
 → compare avg score
   shadow - parent ≥ +0.5 → PROMOTE  (shadow replaces parent)
   shadow - parent < 0.5  → DEMOTE   (shadow retired to .rejected.md)
```

### Grading view

Each session writes `.multi_agent/sessions/<id>/REPORT.html` with:

- Sparkline per agent showing score trend
- Radar chart: completeness / format / quality
- Top failed checklist items with evidence
- Skill usage heatmap
- `--trend` generates a cross-session `TREND.html` aggregating all above

## Environment variables (optional)

```bash
export MULTI_AGENT_FLOW=task-based         # pipeline variant
export MULTI_AGENT_MAX_CONCURRENT=3        # parallel Claude calls
export MULTI_AGENT_CALL_SPACING_MS=100     # min ms between calls
export MULTI_AGENT_AUTO_COMMIT=1           # auto-commit Dev step
export MULTI_AGENT_NO_AUTO_FEEDBACK=0      # skip post-build Maestro/logcat
export MULTI_AGENT_AUTO_HEAL=1             # auto re-run on BLOCKERs
export MULTI_AGENT_SKILL_REVIEW=0          # 1 = prompt before writing new skills
export MULTI_AGENT_CRITIC_ALL=0            # 1 = run Critic for every step (legacy; default only dev+test)
export MULTI_AGENT_TL_CRITIC_ALWAYS=0      # 1 = always run Critic for TechLead (ignores complexity/type gate)
export MULTI_AGENT_TL_CRITIC_NEVER=0       # 1 = never run Critic for TechLead (cheapest)
```

## License

MIT
