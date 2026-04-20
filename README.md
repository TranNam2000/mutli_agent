# Multi-Agent Product Development Pipeline

A task-based AI pipeline that turns a product idea or bug report into classified tasks, prioritizes them by resource/business value, implements them, and verifies the output — all via specialized Claude agents with self-improving rules and skills.

## Flow

```
Input (task / feature / bug / URL)
    ↓
PM (route) → kind = feature | bug_fix | ui_tweak | refactor | investigation
    │         + confidence + reason; sub-pipeline + fast-track gate selected
    │         ▼
    │   [authoritative for `scope` in task metadata]
    ↓
BA (classify) → structured Task list + ```json META``` block per task
    │           (metadata: context / flow_control / technical_debt)
    ↓
Design (reuse-first) → UI tasks routed here; check existing DS → reuse or create
    ↓
BA (consolidate) → merge design refs back into task list
    ↓
TechLead (prioritize) → enrich_metadata(impact_area, risk_bump)
    │                  → validate estimates + sprint packing
    │                  → conditional Critic (complexity/type/core-file gate)
    ↓
Dev ∥ Test (parallel) → implementation + test plan in priority order
    ↓
QA → Dev fix loop → Patrol + Maestro auto-run
    │               ↓
    │         ┌─ BLOCKER on a task that had Critic skipped upstream? ─┐
    │         ↓                                                       │
    │   🚨 EMERGENCY AUDIT MODE                                       │
    │     1. audit_log.jsonl records RCA entry                        │
    │     2. Critic forced ON for rest of session                     │
    │     3. IntegrityRules: module blacklist, keyword risk,          │
    │        agent reputation bumped                                  │
    │     4. integrity.md regenerated                                 │
    └─────────────────────────────────────────────────────────────────┘
    ↓
Auto-feedback → build app, E2E, screenshot diff, logcat → self-heal if BLOCKERs
    ↓
RuleOptimizer (LLM) + suggest_from_integrity (zero-token)
    ↓
HTML dashboard + meta-learning (rules/skills tune themselves)
```

### Sub-pipeline per PM `kind`

| kind            | Steps run                                                   |
|-----------------|-------------------------------------------------------------|
| `feature`       | BA → Design → TechLead → test_plan → Dev → Test (full)      |
| `bug_fix`       | Dev → Test                                                  |
| `ui_tweak`      | Design → Dev → Test                                         |
| `refactor`      | TechLead → Dev → Test                                       |
| `investigation` | Code Investigator only — direct answer, no pipeline         |

PM uses a heuristic keyword pass first (Vietnamese diacritics aware); only
falls back to an LLM call when the signal is mixed. When confidence `< 0.6`
the CLI asks the user to confirm the kind before dispatching. PM then stamps
its `kind` onto every task's `metadata.context.scope` — downstream gates,
audit log, and RuleOptimizer all read from there (single source of truth).

## Task Metadata — the "nervous system"

Every task carries a structured JSON metadata block emitted by BA and enriched
by TechLead. The orchestrator reads this metadata at every skip/run decision,
the audit log captures it when a failure happens, and the learning tables
consume it across sessions.

```json
{
  "task_id": "BUG-12345",
  "context": {
    "scope":      "feature | bug_fix | hotfix | refactor | ui_tweak",
    "priority":   "P0 | P1 | P2 | P3",
    "risk_level": "low | med | high",
    "complexity": "S | M | L | XL"
  },
  "flow_control": {
    "skip_critic":   ["PM", "BA", "TechLead"],
    "require_qa":    true,
    "max_revisions": 2
  },
  "technical_debt": {
    "impact_area":     ["ui", "payment", "auth", "api", "..."],
    "legacy_affected": false
  }
}
```

**Who fills what:**

| Field                             | Authoritative writer                              |
|-----------------------------------|---------------------------------------------------|
| `context.scope`                   | **PM** (overrides any BA value after parse)       |
| `context.priority`                | BA                                                |
| `context.complexity`              | BA, adjusted by TechLead                          |
| `context.risk_level`              | BA, bumped by TechLead when `impact_area` hits core/payment/auth |
| `technical_debt.impact_area`      | **TechLead** (`enrich_metadata` keyword match)    |
| `flow_control.skip_critic`        | BA (guided by prompt rules)                       |

**Decision helpers** used by the orchestrator's Critic gate:

- `is_low_risk_small()` — `complexity=S` AND `risk_level=low`
- `is_hot_p0()` — `scope=hotfix` AND `priority=P0`
- `touches_core()` — `impact_area` contains `core`, `payment`, `auth`, or `security`

## Emergency Audit Mode

When QA surfaces BLOCKERs on a task that had Critic skipped upstream, the
orchestrator auto-activates Full-Audit Mode:

1. Records a JSONL row in `<session>/audit_log.jsonl` and rolls it up into
   the profile-level `rules/<profile>/.audit/aggregate.jsonl`.
2. Sets `_emergency_audit=True` → every subsequent step forces Critic on.
3. Upgrades the offending task's `risk_level` to `high` and clears its
   `skip_critic` list in-memory.
4. Calls `IntegrityRules.record_failure()`:
   - `module_blacklist[impact_area] += 1`
   - `agent_reputation[role].false_negatives += 1` (and starts a
     forced-Critic window of 5 tasks when threshold is crossed)
   - `keyword_risk[matched]` promoted to `high`/`med`
5. Regenerates `rules/<profile>/integrity.md` — a human-readable summary
   of all three learning tables.

## Self-evolving rules

RuleOptimizer has two sources of suggestions merged each session:

- **LLM-driven** (paid): analyses `critic_reviews`, chronic patterns and
  "easy items" and asks Claude to propose rule edits.
- **Integrity-driven** (free, new): `suggest_from_integrity()` reads the
  three learning tables and deterministically emits ADD suggestions like:
  - "Tasks touching `payment` MUST set `risk_level=high` and MUST NOT list
    BA / TechLead / PM in `skip_critic`."
  - "BA has 2 false-negatives on record — must justify skip_critic in the
    task description before suggesting it again."

Both streams pass through `ReviseHistory` dedup + regression check +
auto-apply threshold, so noisy suggestions never ship.

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

- **PM router** — classifies every request into 5 kinds and dispatches a
  sub-pipeline; PM owns the `scope` field of task metadata
- **Task metadata** — structured JSON block per task drives all skip/run
  decisions and powers cross-session analytics
- **Fast-Track Critic gate** — skips Critic for PM/BA/Design/TechLead on
  low-risk small tasks; cuts ~30–40% of Critic LLM calls
- **Emergency Audit Mode** — any QA blocker on a skipped-Critic task auto-
  triggers Full-Audit, records RCA, and forces Critic on for the rest of
  the session
- **IntegrityRules** — persistent module blacklist / keyword risk / agent
  reputation tables that gate future skip decisions; cross-session learning
  from false-negatives
- **RuleEvolver** — unified rule evolution loop (default ON): merges 4
  signals (LLM analysis / IntegrityRules / user feedback / token-cost
  trend), provenance-tags every clause, multi-dim scores each proposal,
  and routes to auto / shadow / pending lanes
- **Statistical shadow A/B** — ≥ 10 sessions per variant, delta must beat
  both 1.0 absolute and 2 × pooled SEM (≈ 95% CI), plus a 7.0 quality
  floor, plus variance ≤ 1.5 stddev — so promotions are signal, not noise
- **Adaptive cost signal** — per (agent × scope × complexity) token
  budget table with rolling-window trend; legitimate XL work never
  flagged, single outliers never rewrite rules
- **User feedback** — `mag feedback <session> --agent X --rating N
  --comment "..."` feeds the learning loop with the highest-trust source
- **Multi-skill per agent** — agents can activate 1..N skills (stack +
  domain + mode). Default heuristic picker is free; opt-in LLM auto-pick
  via `MULTI_AGENT_SKILL_LLM=1` costs ~5k tok/session for metadata-aware
  routing
- **Task classification** — BA outputs structured tasks with type/priority/complexity/risk/business-value
- **Reuse-first design** — Design checks existing design system before creating new specs
- **Resource-aware scheduling** — TechLead validates BA estimates, bin-packs tasks into sprints
- **Parallel Dev + Test** — test plan in priority order: P0 full coverage, P3 smoke only
- **Auto-feedback loop** — Patrol + Maestro + logcat scraper + vision-diff vs design specs → self-heal BLOCKERs
- **Maintain mode** — auto-detect project, scoped keyword-driven context, git branch per session
- **Meta-learning** — rules and skills tune themselves over sessions: auto-apply, shadow A/B, criteria upgrade, regression rollback
- **Skill system** — 30 specialized skills per agent × scope (simple → feature → full_app → bug_fix), stack-specific (Flutter / React Native / Next.js / NestJS) + domain (e-commerce) + mode (startup MVP / maintain / hotfix)
- **TL ↔ BA feedback loops** — proactive batch review when BA spec has
  red flags + reactive postmortem when Dev fix loops stuck on same
  BLOCKER
- **Context Cohesion check** — flags tasks that mention libraries not in
  declared project deps (Firebase spec but no `firebase_core` in pubspec)
- **HTML dashboard** — self-contained report with score trends, skill heatmap, Maestro thumbnails, feedback items

## Architecture

```
multi_agent/
├── main.py                  # CLI entry
├── orchestrator.py          # pipeline core (PM router, gates, audit mode)
├── agents/
│   ├── pm_agent.py          # router: 5 kinds + dispatch plan
│   ├── ba_agent.py          # task producer (emits META blocks)
│   ├── techlead_agent.py    # enrich_metadata + prioritize + sprint
│   ├── design_agent.py
│   ├── dev_agent.py
│   ├── test_agent.py
│   ├── critic_agent.py
│   ├── rule_optimizer_agent.py  # LLM + suggest_from_integrity
│   ├── skill_designer_agent.py
│   └── investigation_agent.py
├── core/                    # message_bus, token_tracker, plan_detector, doctor
├── context/                 # project_detector, scoped_reader, git_helper, health_check
├── learning/
│   ├── task_models.py       # Task + metadata parse/emit
│   ├── task_metadata.py     # TaskMetadata (context/flow_control/technical_debt)
│   ├── audit_log.py         # RCA JSONL writer + aggregator
│   ├── integrity_rules.py   # module blacklist / keyword risk / reputation
│   ├── rule_evolver.py      # 4-source merge, provenance, multi-dim, A/B
│   ├── skill_selector.py    # per-agent + multi-skill + LLM auto-pick
│   ├── skill_optimizer.py   # shadow A/B for skills
│   ├── revise_history.py    # score trend, regression detection, rollback
│   └── score_adjuster.py    # cost + clarification + test-outcome penalties
├── testing/                 # patrol_runner, maestro_runner, stitch_browser, auto_feedback
├── reporting/               # html_report, trend_report
├── rules/                   # system prompts + criteria per agent
│   └── <profile>/
│       ├── pm.md / ba.md / techlead.md / dev.md / test.md / ...
│       ├── <agent>.shadow.md  # rule A/B test variant (auto-managed)
│       ├── <agent>.rejected.md # archived loser of A/B (auto-managed)
│       ├── criteria/<agent>.md
│       ├── integrity.md     # auto-generated from IntegrityRules
│       ├── .learning/
│       │   ├── module_blacklist.json
│       │   ├── keyword_risk.json
│       │   ├── agent_reputation.json
│       │   ├── cost_history.json   # per-agent rolling token ratios
│       │   └── cost_budgets.json   # user override (optional)
│       ├── .audit/          # cross-session audit aggregate
│       ├── .feedback/       # mag feedback <session>.jsonl entries
│       └── .shadow_log.json # rule A/B baseline vs shadow score log
└── skills/                  # 30 specialized skill files per agent × scope
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

### 4. Learning system (unified, default ON)

After every pipeline run the learning system consolidates four signal
sources into one rule-evolution stream — no separate path, no opt-in:

1. **LLM analysis** of critic REVISE patterns (`RuleOptimizerAgent`)
2. **IntegrityRules tables** — module blacklist, keyword risk, agent
   reputation (zero-token deterministic suggestions)
3. **User feedback** via `mag feedback <session> --agent X --rating N
   --comment "..."` (read from `rules/<profile>/.feedback/*.jsonl`)
4. **Cost signals** from ScoreAdjuster (per-agent token pressure)

Every suggestion is tagged with **provenance** (source + session + ts +
score) as an inline HTML comment inside the rule file, so any clause
can be traced back to its origin.

Each proposal is then scored along **4 dimensions**:

| Dim           | Weight | Meaning                                   |
|---------------|--------|-------------------------------------------|
| correctness   | 0.40   | Does it reduce real errors?               |
| consistency   | 0.30   | Does it contradict / duplicate existing?  |
| usability     | 0.15   | Does it reduce downstream clarifications? |
| cost          | 0.15   | Does it avoid token bloat?                |

Consensus boost: when ≥ 2 sources propose the same change, multi-dim
score is multiplied by up to 1.2×.

Routing by final score:

| Lane        | Threshold                               | Action                      |
|-------------|-----------------------------------------|-----------------------------|
| **auto**    | score ≥ 0.80 AND ≥ 3 consensus sources  | Append to rule file now     |
| **shadow**  | score ∈ [0.60, 0.80)                    | Write `<agent>.shadow.md`, A/B test |
| **pending** | score < 0.60                            | Queue for user review       |

Shadow A/B (rule variants) — statistical, not demo-grade:
- Each session either loads the baseline rule or `<agent>.shadow.md`
  (orchestrator balances samples so both variants accumulate evenly).
- After each session the agent's average critic score is logged against
  whichever variant it ran.
- A verdict is only rendered when:
  - ≥ **10 sessions per variant**
  - Variance ≤ **1.5 stddev** in both (otherwise noise dominates)
  - max(baseline_avg, shadow_avg) ≥ **7.0** quality floor (so promoting
    isn't just 'less bad')
  - Delta beats **both** 1.0 absolute AND 2 × pooled SEM (≈ 95% CI)
- Then:
  - shadow − baseline ≥ threshold → PROMOTE (shadow replaces baseline;
    old baseline archived as `<agent>.rejected.md`)
  - shadow − baseline ≤ −threshold → DEMOTE (shadow deleted)
  - otherwise → keep testing
- A rejected comparison carries a `reject_reason` field for audit
  (insufficient samples / high variance / below quality floor).

Classic meta-learning triggers (still active):

| Trigger                                           | Action                                  |
|---------------------------------------------------|-----------------------------------------|
| Applied rule causes score drop ≥ 0.5 (2+ sessions)| Auto-rollback + blacklist pattern       |
| Agent avg ≥ 8.5 for 3 consecutive sessions        | Auto-upgrade PASS_THRESHOLD +1          |
| Skill avg < 5.0 across ≥ 5 uses                   | Auto-deprecate (if other skills exist)  |
| Skill stuck in 5-7 band across 4+ uses            | Auto-refine via shadow A/B              |
| Chronic misfit pattern ≥ 4 sessions               | Auto-create new shadow skill            |
| Two skills with ≥ 70% trigger overlap             | Auto-merge candidate                    |
| Module accumulates ≥ 3 post-skip failures         | `module_blacklist` → force Critic       |
| Agent accumulates ≥ 2 false-negatives             | `agent_reputation` force-Critic window 5 |
| Keyword appears in ≥ 1 failure blocker            | `keyword_risk` promote to med/high      |

Set `MULTI_AGENT_LEGACY_RULE_OPTIMIZER=1` to restore the pre-evolver
auto-apply-on-repeat behaviour (useful only for debugging).

**Adaptive cost signal** — one of the four learning inputs deserves its
own note. Rather than a flat token threshold, the system computes an
expected budget from the current task batch's metadata:

```
expected_tokens(agent) = Σ EXPECTED_BUDGET[(agent, task.scope, task.complexity)]
                              for each task in the batch
```

A suggestion is emitted only when BOTH hold:

1. `actual / expected ≥ 1.5×` this session
2. ≥ 3 of the last 5 sessions also ≥ 1.5×

Per-agent ratio history lives in
`rules/<profile>/.learning/cost_history.json`. Users can override the
defaults by writing `rules/<profile>/.learning/cost_budgets.json`
with keys of the form `"dev|feature|XL": 50000`.

Result: legitimate XL work (e.g. Dev spending 40k on a full-app task)
is never flagged as over-budget, while an agent that consistently
bloats S-task output across several sessions does trigger a
"trim output" rule clause — tagged with `src=cost` provenance.

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
export MULTI_AGENT_LEGACY_RULE_OPTIMIZER=0 # 1 = use pre-RuleEvolver rule update path (debug only)
export MULTI_AGENT_SKILL_LLM=0             # 1 = let Claude pick 1..MAX skills (costs ~5k tok/session)
export MULTI_AGENT_SKILL_MAX=2             # cap active skills per agent (default 2; 1 = single)
```

### User feedback — close the loop with one command

After a session finishes, if the output missed the mark, file a rating
so the learning system picks it up:

```bash
mag feedback 20260420_063112_abcd --agent ba --rating 2 \
    --comment "AC thiếu edge case về offline retry"
```

Entries land in `rules/<profile>/.feedback/*.jsonl`. On the next
session, `RuleEvolver` translates low-rating aggregates into
`SRC_USER`-tagged suggestions with the highest source weight, feeding
them through the same merge + multi-dim scoring + shadow A/B pipeline
as LLM and IntegrityRules suggestions.

## License

MIT
