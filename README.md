# Multi-Agent Product Development Pipeline

> Autonomous multi-agent framework that turns a product idea or bug
> report into shipped code — classifying the request, routing it through
> specialized Claude agents (PM → BA → Design → TechLead → Dev → QA),
> and teaching itself from every session's outcome.

The killer feature is **cross-session learning**: every skip-Critic
decision that later causes a QA failure is logged, every module that
accumulates failures is blacklisted, every user rating is merged into
the rule files — so the pipeline literally never repeats the same
mistake twice.

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Pipeline flow](#pipeline-flow)
- [PM router — 5 request kinds](#pm-router--5-request-kinds)
- [Task metadata — the nervous system](#task-metadata--the-nervous-system)
- [Fast-Track + Emergency Audit](#fast-track--emergency-audit)
- [Learning system](#learning-system)
- [Skill system](#skill-system)
- [User feedback](#user-feedback)
- [Environment variables](#environment-variables)
- [Architecture](#architecture)
- [When to use / when not to](#when-to-use--when-not-to)
- [License](#license)

---

## Install

```bash
git clone https://github.com/TranNam2000/mutli_agent
cd mutli_agent
pip install -e .
```

Requires Python ≥ 3.10 and the `claude` CLI installed + authenticated.
Optional: `patrol`, `maestro`, `flutter`, `git`, `adb`, `rg`.

Run `mag --doctor` to verify every dependency at once.

---

## Quick start

```bash
# From any project folder
cd ~/my_flutter_app

# Build a feature
mag "add Google OAuth login"

# Fix a reported bug
mag "fix Stripe checkout alignment on iPhone 15 Pro Max"

# Tweak UI only
mag "change home screen padding + dark mode toggle"

# Quick MVP with custom resources
mag "build MVP video downloader" --dev-slots 3 --sprint-hours 100

# Resume a paused session
mag --list
mag --resume 20260419_180000_a3b2

# Cross-session trend dashboard
mag --trend

# Rate a session to feed the learning loop
mag feedback 20260419_180000_a3b2 --agent ba --rating 2 \
    --comment "AC missed the offline-retry edge case"
```

Every run writes `.multi_agent/sessions/<session_id>/REPORT.html` — a
single-file dashboard with sparklines, radar charts, audit entries,
and Maestro test thumbnails.

---

## Pipeline flow

```
Input (task / bug / URL)
    │
    ▼
PM router ─── classifies into 5 kinds, stamps task.metadata.scope
    │
    ▼  (route dispatches a sub-pipeline — see table below)
BA (task list)        ← emits ```json META``` per task
    │
    ▼
Design (reuse-first)  ← maps UI tasks to existing design system when possible
    │
    ▼
BA (consolidate)      ← merges design refs back into the task list
    │
    ▼
TechLead              ← enrich_metadata(impact_area, risk_bump)
    │                    prioritize + sprint pack
    │                    conditional Critic (complexity/type/core-file)
    │                    Option B spec review ──► asks BA for clarifications
    │                                             when smell test flags tasks
    ▼
Dev ∥ Test (parallel) ← Dev Critic mandatory; QA plan in priority order
    │
    ▼
QA → Dev fix loop ─── Patrol + Maestro + vision diff + logcat
    │                    Option C postmortem ──► TL asks BA to rewrite spec
    │                                             when Dev stuck 2 rounds
    │   ┌─ BLOCKER on a task that had Critic skipped upstream? ──┐
    │   │                                                         │
    │   ▼                                                         │
    │ 🚨 EMERGENCY AUDIT MODE                                      │
    │   1. audit_log.jsonl records RCA entry                      │
    │   2. Critic forced ON for the rest of the session           │
    │   3. IntegrityRules mutate (module / keyword / reputation)  │
    │   4. integrity.md regenerates                                │
    └──────────────────────────────────────────────────────────────┘
    │
    ▼
Learning system ──── 4-source merge (LLM + Integrity + User Feedback + Cost)
    │                    provenance-tag every clause
    │                    multi-dim score (correctness / consistency /
    │                                       usability / cost)
    │                    route: auto-apply / shadow A/B / pending
    │                    evaluate live shadows → promote or demote
    ▼
HTML dashboard + committed code
```

---

## PM router — 5 request kinds

```
feature       → BA → Design → TechLead → test_plan → Dev → Test   (full)
bug_fix       → Dev → Test
ui_tweak      → Design → Dev → Test
refactor      → TechLead → Dev → Test
investigation → InvestigationAgent only (direct Q&A, no build)
```

The router uses a keyword heuristic (Vietnamese diacritics aware)
first; only falls back to an LLM call when the signal is genuinely
mixed. When `confidence < 0.6` the CLI shows the candidates and asks
the user to pick. Once a kind is chosen, it is stamped onto every
task's `metadata.context.scope` — downstream gates, audit log, and the
learning system all read from that single source of truth.

---

## Task metadata — the nervous system

Every Task carries a structured metadata block embedded in the BA
markdown as a ```` ```json META ```` fenced block:

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

**Who fills what**

| Field                          | Authoritative writer                        |
|--------------------------------|---------------------------------------------|
| `context.scope`                | **PM** (overrides any BA value post-parse)  |
| `context.priority`             | BA                                          |
| `context.complexity`           | BA, adjusted by TechLead                    |
| `context.risk_level`           | BA, bumped by TechLead on core/payment/auth |
| `technical_debt.impact_area`   | **TechLead** (`enrich_metadata` keywords)   |
| `flow_control.skip_critic`     | BA (guided by the system prompt)            |

**Decision helpers** used by the orchestrator's Critic gate:

- `is_low_risk_small()` — `complexity=S AND risk_level=low`
- `is_hot_p0()` — `scope=hotfix AND priority=P0`
- `touches_core()` — `impact_area` contains `core | payment | auth | security`

Legacy BA output (no META block) is handled by
`learning.task_metadata.derive_from_task()`, which maps existing Task
fields into a sensible default. No BC break.

---

## Fast-Track + Emergency Audit

Fast-Track is the default posture. By metadata-driven rules, Critic is
**skipped** for PM / BA / Design / TechLead on low-risk small tasks,
and **mandatory** for Dev + Test regardless. This cuts 30–40% of
Critic LLM calls per session on average.

If QA then surfaces a BLOCKER on a task that had Critic skipped
upstream, **Emergency Audit Mode** fires:

1. `audit_log.jsonl` records the RCA entry (predicted metadata vs
   actual outcome, which roles were skipped, first blocker string).
2. `self._emergency_audit = True` — every subsequent step in this
   session forces Critic on, no exceptions.
3. The offending task is bumped to `risk_level=high` in memory and its
   `skip_critic` list is cleared.
4. `IntegrityRules.record_failure()`:
   - `module_blacklist[area] += 1`
   - `agent_reputation[role].false_negatives += 1` (and starts a
     forced-Critic window of 5 tasks on the Nth offense)
   - `keyword_risk[matched]` promoted to `med` / `high`
5. `rules/<profile>/integrity.md` is regenerated with the new state.

The next session loading this profile sees the updated blacklist,
reputation, and keyword risks — and routes around the landmine
automatically.

### TechLead conditional Critic

Even inside Fast-Track, TechLead's output is critiqued whenever:

| Signal                                       | Critic |
|----------------------------------------------|--------|
| `MULTI_AGENT_TL_CRITIC_ALWAYS=1`             | RUN    |
| `MULTI_AGENT_TL_CRITIC_NEVER=1`              | SKIP   |
| any task has `complexity=L | XL`             | RUN    |
| any task has `type=bug | hotfix`             | RUN    |
| all tasks `complexity ∈ {S, M}` on non-bug   | SKIP   |
| output mentions `main.dart | app_router | service_locator | Base*` | RUN |
| otherwise                                    | SKIP   |

---

## Learning system

One unified loop, default ON. Replaces the legacy
auto-apply-on-repeat path (kept behind `MULTI_AGENT_LEGACY_RULE_OPTIMIZER=1`
as a debug escape hatch).

### Four signal sources merged every session

1. **LLM analysis** of Critic REVISE patterns (`RuleOptimizerAgent`)
2. **IntegrityRules tables** — module blacklist, keyword risk, agent
   reputation (zero-token deterministic suggestions)
3. **User feedback** via `mag feedback` (highest source weight)
4. **Cost signals** — per-agent token spend vs metadata-driven expected
   budget, evaluated across a rolling 5-session window

### Provenance

Every clause appended to a rule file carries an inline HTML comment:

```markdown
<!-- provenance: src=llm+integrity+user session=20260420_abcd ts=2026-04-20T06:31:12 score=0.87 -->
- Tasks touching `payment` MUST set risk_level=high and MUST NOT list
  BA / TechLead / PM in flow_control.skip_critic.
```

`learning.rule_evolver.parse_provenance_from_rule()` reads these markers
back for audit and `mag --trend` rendering.

### Multi-dim scoring

Every suggestion is scored across four dimensions:

| Dimension      | Weight | Meaning                                     |
|----------------|--------|---------------------------------------------|
| correctness    | 0.40   | Does it actually reduce errors?             |
| consistency    | 0.30   | Does it contradict / duplicate existing?    |
| usability      | 0.15   | Does it reduce downstream clarifications?   |
| cost           | 0.15   | Does it avoid token bloat?                  |

Consensus boost: when ≥ 2 sources propose the same change, the final
score is multiplied by up to 1.2×.

### Lane routing

| Lane        | Condition                               | Action                              |
|-------------|-----------------------------------------|-------------------------------------|
| **auto**    | score ≥ 0.80 AND ≥ 3 consensus sources  | Append to rule file now             |
| **shadow**  | score ∈ [0.60, 0.80)                    | Write `<agent>.shadow.md`, A/B test |
| **pending** | score < 0.60                            | Queue for user review               |

### Statistical shadow A/B

Shadow rule variants don't promote on demo-grade thresholds. A verdict
is only rendered when all of:

- **≥ 10 sessions per variant** accumulated
- **Variance ≤ 1.5 stddev** in both (else noise dominates)
- **max(baseline_avg, shadow_avg) ≥ 7.0** quality floor (so promoting
  isn't just "less bad")
- Delta beats **both** 1.0 absolute AND 2 × pooled SEM (≈ 95% CI)

Then:

- shadow − baseline ≥ threshold → **PROMOTE** (shadow replaces
  baseline; old baseline archived as `<agent>.rejected.md`)
- shadow − baseline ≤ −threshold → **DEMOTE** (shadow deleted)
- otherwise → keep testing

Rejected comparisons carry a `reject_reason` field for audit
(insufficient samples / high variance / below quality floor).

### Adaptive cost signal

Rather than a flat token threshold, the system derives expected spend
from the current task batch's metadata:

```
expected_tokens(agent) = Σ EXPECTED_BUDGET[(agent, task.scope, task.complexity)]
                              for each task in the current batch
```

A cost-driven rule suggestion is emitted only when BOTH:

1. `actual / expected ≥ 1.5×` this session
2. ≥ 3 of the last 5 sessions also ≥ 1.5×

Per-agent ratio history lives in
`rules/<profile>/.learning/cost_history.json`. Users can override the
defaults by writing `rules/<profile>/.learning/cost_budgets.json`:

```json
{
  "dev|feature|XL": 50000,
  "test|feature|XL": 30000
}
```

Legitimate XL work (Dev spending 40k on a full-app task) is never
flagged as over-budget. Only consistent output bloat across several
sessions triggers a "trim output" clause — tagged `src=cost` provenance.

### TL ↔ BA feedback loops

The learning signal is not purely post-session. Two in-session loops
catch bad specs before Dev wastes a round:

- **Option B — proactive batch review**. Right after BA produces
  tasks, TechLead runs a regex smell test on every task. Red flags:
  - `len(acceptance_criteria) < 2`
  - description < 80 chars
  - `MISSING_INFO` still present
  - vague phrasing ("as usual", "works normally", "như thường")
  - Context Cohesion: task mentions a library (Firebase, Stripe,
    Riverpod, Sentry, next-auth, Prisma, 38 total…) that isn't in the
    project's declared dependencies
  - hotfix task with zero AC
  - core-area + L / XL complexity
  
  When any fires, TL sends ONE consolidated question to BA (a single
  LLM call) and patches the answers back into each task's AC and
  description. Spec-clean sessions pay zero tokens.

- **Option C — reactive postmortem**. If the QA→Dev fix loop surfaces
  the same BLOCKER in two consecutive rounds, TL asks BA to reflect on
  whether the spec itself was incomplete. BA rewrites the affected
  tasks (preserving unaffected ones verbatim), and Dev re-implements
  using the refined spec. Guarded by `_ba_postmortem_fired` so it
  fires at most once per session.

### Meta-learning triggers (cross-session)

| Trigger                                             | Action                                     |
|-----------------------------------------------------|--------------------------------------------|
| Applied rule causes score drop ≥ 0.5 (2+ sessions)  | Auto-rollback + blacklist pattern          |
| Agent avg ≥ 8.5 for 3 consecutive sessions          | Auto-upgrade PASS_THRESHOLD +1             |
| Skill avg < 5.0 across ≥ 5 uses                     | Auto-deprecate (if other skills exist)     |
| Skill stuck in 5-7 band across 4+ uses              | Auto-refine via shadow A/B                 |
| Chronic misfit pattern ≥ 4 sessions                 | Auto-create new shadow skill               |
| Two skills with ≥ 70% trigger overlap               | Auto-merge candidate                       |
| Module accumulates ≥ 3 post-skip failures           | `module_blacklist` → force Critic          |
| Agent accumulates ≥ 2 false-negatives               | `agent_reputation` force-Critic window 5   |
| Keyword appears in ≥ 1 failure blocker              | `keyword_risk` promote to med/high         |

---

## Skill system

30 specialized skill files across six agents. The orchestrator picks
one (or more) per session via keyword heuristic first; LLM fallback
when the signal is mixed.

**Axes of differentiation**

```
                 ┌── agent    (6): PM / BA / Design / TL / Dev / Test
                 │
 each skill      ├── scope    (5): simple / bug_fix / feature / module / full_app
  tagged with    │
                 ├── stack    (n): Flutter / React Native / Next.js / NestJS
                 │
                 ├── domain   (n): e-commerce (more over time)
                 │
                 └── mode     (n): startup_mvp / maintain_project / hotfix_emergency
```

**Multi-skill** — agents can activate up to `MULTI_AGENT_SKILL_MAX`
skills at once. The default heuristic picker keeps a secondary only
when its score ≥ 70 % of the primary. Set `MULTI_AGENT_SKILL_LLM=1` to
let Claude pick 1 to MAX skills per step (adds ~5k tokens/session but
understands cross-concern tasks like NestJS + e-commerce).

When metadata is available (TL / Design / Dev / Test steps), the LLM
picker receives a compact summary — scope / max_risk / max_complexity /
impact_area / integrity alerts / emergency_audit flag — so it can
route on semantic signals rather than keyword text.

---

## User feedback

Close the learning loop with one command:

```bash
mag feedback 20260420_063112_abcd \
    --agent ba \
    --rating 2 \
    --comment "AC missed the offline-retry edge case"
```

Entries land in `rules/<profile>/.feedback/<session_id>.jsonl`. On the
next session, `RuleEvolver.FeedbackStore.suggestions_from_feedback()`
aggregates low-rating comments per agent and emits `SRC_USER`-tagged
rule-ADD suggestions — which carry the highest source weight in the
multi-dim scorer.

Rating values: 1 (very bad) to 5 (very good). Agents with mean rating
≥ 3.5 are left alone; below that, a tighten-behaviour clause is
proposed with the digest of the recent low-rating comments.

---

## Environment variables

All are optional.

```bash
# Pipeline basics
export MULTI_AGENT_FLOW=task-based          # pipeline variant
export MULTI_AGENT_MAX_CONCURRENT=3         # parallel Claude calls
export MULTI_AGENT_CALL_SPACING_MS=100      # min ms between calls
export MULTI_AGENT_AUTO_COMMIT=1            # auto-commit Dev step
export MULTI_AGENT_NO_AUTO_FEEDBACK=0       # skip post-build Maestro/logcat
export MULTI_AGENT_AUTO_HEAL=1              # auto re-run on BLOCKERs
export MULTI_AGENT_SKILL_REVIEW=0           # 1 = prompt before writing new skills

# Critic gating
export MULTI_AGENT_CRITIC_ALL=0             # 1 = Critic everywhere (legacy)
export MULTI_AGENT_TL_CRITIC_ALWAYS=0       # 1 = always Critic TechLead
export MULTI_AGENT_TL_CRITIC_NEVER=0        # 1 = never Critic TechLead (cheapest)

# Learning system
export MULTI_AGENT_LEGACY_RULE_OPTIMIZER=0  # 1 = pre-RuleEvolver path (debug)

# Skill selection
export MULTI_AGENT_SKILL_LLM=0              # 1 = Claude picks skills (~5k tok)
export MULTI_AGENT_SKILL_MAX=2              # cap active skills (1 = single)
```

---

## Architecture

```
multi_agent/
├── main.py                   CLI entry; mag run / --resume / --trend / feedback
├── orchestrator.py           Pipeline core: PM router, sub-pipeline dispatch,
│                             Fast-Track gate, Emergency Audit Mode, Rule A/B
│                             variant activation, shadow score logging
├── agents/
│   ├── pm_agent.py           Router: 5 kinds + dispatch plan + scope stamp
│   ├── ba_agent.py           Task producer (emits ```json META```)
│   ├── techlead_agent.py     enrich_metadata + B batch review + prioritize
│   ├── design_agent.py       Reuse-first design; process_ui_tasks
│   ├── dev_agent.py          Implementation + revise + widget-key injection
│   ├── test_agent.py         QA plan + review + loop
│   ├── critic_agent.py       3-level grading (FULL / PARTIAL / MISS)
│   ├── rule_optimizer_agent  LLM suggestions + suggest_from_integrity
│   ├── skill_designer_agent  Shadow skill creation
│   └── investigation_agent   Codebase Q&A (for kind=investigation)
│
├── core/                     message_bus / token_tracker / doctor /
│                             plan_detector
│
├── context/                  project_detector / scoped_reader / git_helper /
│                             health_check / context_builder
│
├── learning/
│   ├── task_models.py         Task dataclass + parse_tasks + emit META
│   ├── task_metadata.py       TaskMetadata + derive_from_task + render/parse
│   ├── audit_log.py           JSONL writer, session + profile-level aggregate
│   ├── integrity_rules.py     module blacklist / keyword risk / reputation
│   ├── rule_evolver.py        RuleEvolver: 4-source merge, provenance,
│   │                          multi-dim scoring, shadow A/B, feedback store
│   ├── skill_selector.py      select_skills, multi-skill + LLM picker
│   ├── skill_optimizer.py     skill shadow A/B + deprecate + merge
│   ├── revise_history.py      score trend, regression detection, rollback
│   └── score_adjuster.py      cost + clarification + test-outcome penalties
│
├── testing/                   patrol_runner / maestro_runner / stitch_browser /
│                              auto_feedback
├── reporting/                 html_report / trend_report
│
├── rules/<profile>/
│   ├── pm.md / ba.md / techlead.md / dev.md / test.md / design.md
│   ├── critic.md / rule_optimizer.md / skill_designer.md / test_plan.md / test_review.md
│   ├── <agent>.shadow.md      Rule A/B variant (auto-managed by RuleEvolver)
│   ├── <agent>.rejected.md    Archived loser of A/B (auto-managed)
│   ├── criteria/<agent>.md    PASS_THRESHOLD + WEIGHTS + checklist
│   ├── integrity.md           Auto-generated IntegrityRules summary
│   ├── .learning/
│   │   ├── module_blacklist.json
│   │   ├── keyword_risk.json
│   │   ├── agent_reputation.json
│   │   ├── cost_history.json   rolling ratios per agent
│   │   └── cost_budgets.json   user overrides (optional)
│   ├── .feedback/              mag feedback <session>.jsonl entries
│   ├── .audit/                 cross-session audit aggregate
│   └── .shadow_log.json        rule A/B baseline vs shadow scores
│
└── skills/                    30 specialized skill files
    ├── pm/           default + startup_mvp + maintain_project + hotfix_emergency
    ├── ba/           simple_feature / feature_module / full_product /
    │                 bug_fix / task_based / ecommerce
    ├── design/       single_screen / feature_screens / design_system
    ├── techlead/     simple_stateful / feature_bloc / full_app_arch /
    │                 react_native_arch / nextjs_arch
    ├── dev/          widget_only / cubit_feature / full_app_implementation /
    │                 react_native / nextjs_app / nestjs_backend
    └── test/         smoke_check / feature_tests / full_regression /
                      jest_rtl / integration_api
```

---

## Scoring mechanism (inside Critic)

Every Critic-enabled agent output is graded against the skill's
criteria file using three levels:

| Grade       | Value | Meaning                                          |
|-------------|-------|--------------------------------------------------|
| **FULL**    | 1.0   | Fully done, good quality                         |
| **PARTIAL** | 0.5   | Done but incomplete / shallow (≥ 50% but < 100%) |
| **MISS**    | 0.0   | Fully missing or wrong                           |

Criteria files live in `rules/<profile>/criteria/<agent>.md`:

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

Final score:

```
dim_score = sum(grades) / total_items × 10        # per dimension
final     = floor(c × Wc + f × Wf + q × Wq)
verdict   = PASS if final ≥ PASS_THRESHOLD else REVISE
```

Auto-penalties override the formula:

- `MISSING_INFO` still in the output → quality capped at 4
- Output shorter than 200 chars → quality capped at 3

Revise loop: on REVISE the agent gets `revision_guide` and retries up
to 2 rounds. After 2 failed rounds the CLI prompts
`[C]ontinue / [R]etry / [S]kip`.

### Score adjuster (post-pipeline)

Critic scores can be gamed by checklist-satisfying output. The
`ScoreAdjuster` blends critic score with real outcomes:

- Patrol / Maestro test fails → penalty on Dev / Design
- Downstream agent asks many clarifications → penalty on upstream
- `MISSING_INFO` leaks into next step → penalty on the producer
- Token usage wildly exceeds expected → penalty on all agents

Dynamic weights by scope:

| Scope      | completeness | format | quality |
|------------|--------------|--------|---------|
| simple     | 0.30         | 0.30   | 0.40    |
| bug_fix    | 0.25         | 0.15   | 0.60    |
| feature    | 0.35         | 0.20   | 0.45    |
| module     | 0.40         | 0.20   | 0.40    |
| full_app   | 0.45         | 0.15   | 0.40    |

### Task priority score (during sprint packing)

```
priority_score = priority_weight × business_value_boost × risk_multiplier / √hours
```

| Field            | Values & multiplier                          |
|------------------|----------------------------------------------|
| priority         | P0=10, P1=6, P2=3, P3=1                      |
| business_value   | critical=1.8, high=1.3, normal=1.0, low=0.6  |
| risk             | low=1.0, med=1.3, high=1.7                   |
| complexity hours | S=3, M=8, L=18, XL=40                        |

Higher score → earlier in sprint; topological sort enforces dependencies.

---

## When to use / when not to

**Use this pipeline when you want:**
- AI to build a feature autonomously overnight / batched
- Audit trail per AI output (compliance, post-mortem, debugging)
- Cross-session learning — after N sessions the pipeline knows your
  codebase's landmines
- Consistent code quality across many features

**Don't use it for:**
- Real-time typing / assist — Cursor / Claude Code do that better
- One-off 1-file scripts — over-engineered for trivial work
- Projects without a stable codebase — IntegrityRules has nothing to
  learn from
- Teams that want a visual editor experience — CLI only today

---

## License

MIT. See [LICENSE](LICENSE).
