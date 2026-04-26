# Multi-Agent Product Development Pipeline

> Autonomous multi-agent framework that turns a product idea or bug
> report into shipped code — classifying the request, routing it through
> specialized Claude agents (PM → BA → Design → TechLead → Dev ∥ Test),
> and teaching itself from every session's outcome.

**Killer feature — cross-session learning.** Every skip-Critic decision
that later causes a QA failure is logged, every module with persistent
failures is blacklisted, every critic score is paired with the real
outcome (test pass rate, clarification count, token ratio) so a
pure-Python logistic regression can gate future rule changes by
predicted regression probability — not by a hand-tuned counter.

---

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [Pipeline flow](#pipeline-flow)
- [PM router — 5 request kinds](#pm-router--5-request-kinds)
- [Scoring — critic + real outcomes](#scoring--critic--real-outcomes)
- [Learning system](#learning-system)
- [Skill system](#skill-system)
- [User feedback](#user-feedback)
- [CLI commands](#cli-commands)
- [Environment variables](#environment-variables)
- [Architecture](#architecture)
- [Testing](#testing)
- [Contributing](#contributing)
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
Optional: `patrol`, `maestro`, `flutter`, `git`, `adb`, `rg` for richer
integration tests and project detection.

Run `mag --doctor` to verify every dependency at once.

---

## Quick start

```bash
# Interactive — paste an idea or a URL (Jira, Confluence, Google Docs…)
python main.py

# Direct — one-shot
python main.py "add OAuth login with Google + Apple for existing Flutter app"

# Maintain mode — explicit project dir (otherwise auto-detected)
python main.py "fix crash when email empty" --maintain /path/to/project

# Resume a session that hit quota or crashed
python main.py --resume 20260422_144550_388d

# Amend a finished session
python main.py --update 20260422_144550_388d "add rate limit to login"

# Re-run with runtime feedback
python main.py --feedback 20260422_144550_388d
```

A session produces checkpoints in `outputs/<project>/` or
`<project>/.multi_agent/sessions/<session_id>/` — every agent's output,
a Markdown conversation log, and a structured JSON export with token
breakdown, review scores, and active skills.

---

## Pipeline flow

```
    ┌──────────┐       ┌──────────────────┐
    │  Input   │──────▶│  PM router       │  classify + pick PM
    │ (text/URL│       │  picks routing   │  routing skill → STEPS
    │  /file)  │       │  skill via MODE: │  declared in skill md
    └──────────┘       └─────────┬────────┘
                                 │
                                 ▼
                       ┌─────────────────────┐
                       │ PM CONFIRM GATE     │  Show user the plan
                       │ Y/n/edit/show       │  before any LLM call
                       └─────────┬───────────┘  on BA/Dev/QA/etc.
                                 │
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
            code_bugfix   code_uitweak       code_feature
            dev,test      design,dev,test    full pipeline
            
            documentation     qa_audit       discovery_research
            ba                investigation  ba (discovery skill)

    Per agent step:
      ┌──────────────────────────────────────────────────────┐
      │ Agent runs claude CLI subprocess  cwd=project_root    │
      │  → LLM sees:                                          │
      │     • base rule + WORKING MODES menu (skills-as-menu) │
      │     • cwd hint (native Read/Edit/Write available)     │
      │     • GROUND RULE (anti-fabrication, 7 luật)          │
      │  → LLM emits MODE:<skill> + reasoning                 │
      │  → If unclear: ASK_USER:<question>                    │
      │     pipeline pauses, prompts user, re-runs (max 2)    │
      │  → If no skill matches: SkillDesigner auto-creates    │
      │     into skills/<agent>/auto/<slug>.md                │
      └──────────────────────────────────────────────────────┘
```

Every edge is a real agent-to-agent message via `core.message_bus` — the
bus now also provides `recent(agent, n=3)` so replies carry context
from prior turns (reducing clarification cascades).

**Anti-hallucination GROUND RULE** is injected on every LLM call:
1. Read/Glob/Grep verify before claiming file/API exists
2. No fabricated code samples — must compile in real project
3. No fabricated facts — citations required (`path:line` format)
4. Unsure → emit `MISSING_INFO: <gì> — MUST_ASK: <user|TL|BA>`
5. Don't understand → emit `ASK_USER: <câu hỏi>`
6. Tool error → accept + report, don't fabricate result
7. Citations format: `lib/auth/oauth_service.dart:42`

---

## PM router — skill-driven routing

The PM agent has 12 routing skills under `skills/pm/routing/`. Each skill
declares its `STEPS:` in frontmatter, so changing the flow = editing
markdown, not Python:

| Skill                 | STEPS                                              | When to apply                       |
|-----------------------|----------------------------------------------------|--------------------------------------|
| `code_feature`        | ba → design → techlead → test_plan → dev → test    | Tính năng mới có code                |
| `code_bugfix`         | dev → test                                         | Fix bug, không design                |
| `code_uitweak`        | design → dev → test                                | Visual cosmetic (color/font/spacing) |
| `documentation`       | ba                                                 | Viết doc, không code                 |
| `qa_audit`            | investigation                                      | Đánh giá doc/code (read-only)        |
| `discovery_research`  | ba (discovery skill)                               | User research / persona / JTBD       |
| `prototype_demo`      | dev → test                                         | POC throwaway code                   |
| `enterprise_b2b`      | full + RBAC checks                                 | SaaS B2B, multi-tenant               |
| `hotfix_emergency`    | dev → test                                         | P0 production down                   |
| `maintain_project`    | ba → dev → test (BA-lite)                          | Refactor/migrate code cũ             |
| `startup_mvp`         | ba → design → tl → dev → test (skip test_plan)     | Lean MVP, ship fast                  |
| `default`             | (LLM step picker)                                  | Fallback nếu skill nào không match   |

PM picks the right skill via `MODE:` tag in its first reply. STEPS come
straight from skill frontmatter — no separate LLM call to pick steps,
saving ~800 tokens per session vs. legacy DISPATCH_PLAN approach.

**PM confirm gate (default ON):** before dispatching agents, PM shows
the user the plan (kind + skill + steps + reason) and asks
`[Y]es / [n]o / [e]dit / [s]how`. Set `MULTI_AGENT_PM_AUTO_CONFIRM=1`
in CI / non-interactive runs to skip.

---

## Scoring — critic + real outcomes

Every Critic step emits a **3-dimension score** (completeness / format /
quality) against a rubric checklist in `rules/<profile>/criteria/*.md`.
Raw scores then pass through `analyzer.score_adjuster` which blends in
**real-world signals**:

1. **Scope reweight** — `simple` / `feature` / `full_app` have different
   dimension weights.
2. **Test outcomes** — Patrol + Maestro pass-rate blended via weighted
   sum (never compound subtraction, so a score can't crash from 8 → 1).
3. **Upstream attribution** — when Dev output has
   `MISSING_INFO: ... MUST_ASK: BA`, ~70 % of the test-fail pressure
   moves onto BA (Dev always keeps ≥ 30 %).
4. **Downstream signals** — clarification count + MISSING_INFO leakage.
5. **Cost penalty** — `used / expected_budget` ratio.

A final "receipt" prints at session end:

```
  Developer score:  8  (critic raw)
    − 3.0  Patrol tests fail 30%
    − 2.0  2 MISSING_INFO leaked downstream
    ───────────────────────────────────────
    = 5  final
```

Every (critic_raw, signals) tuple is appended to
`rules/<profile>/.learning/score_correlation.jsonl`. Run
`mag validate-rubric` after ≥ 3 sessions to see Pearson correlation
between the critic rubric and each real signal — if `|r| < 0.3`, the
rubric isn't predicting reality.

---

## Learning system

Three conceptually-separate layers:

### 1. **IntegrityRules** (always on)

Observes failures → promotes modules / keywords / agents to higher-risk
buckets → forces Critic on for matching tasks in future sessions.
Deterministic, no LLM cost.

### 2. **RuleEvolver** (default: propose)

Merges four signal sources (LLM suggestions, integrity findings, user
feedback from `mag feedback`, cost overages) into a unified decision
stream with multi-dim scoring. Routes each suggestion to one of three
lanes:

- **auto-apply** — ≥ 3 consensus sources AND multi-dim ≥ 0.80
- **shadow A/B** — multi-dim ∈ [0.60, 0.80); gated behind
  `SHADOW_AB_MIN_TOTAL_SESSIONS = 30`
- **pending** — multi-dim < 0.60; surfaced to user for review

### 3. **Regression classifier** (kicks in after ≥ 30 samples)

Pure-Python logistic regression trained on historical apply →
regression labels. Replaces the old "count ≥ 5" gate with a
context-aware `P(regress)` probability:

- `P(regress) < 0.20` → auto-apply
- `< 0.50` → shadow
- else → skip

Run `mag rubric-classifier` to see the current feature weights. Training
happens automatically once per session (< 200 ms for 40 samples).

### Learning modes

Set `MULTI_AGENT_LEARNING_MODE` to one of:

- `propose` (default) — every suggestion surfaced; user confirms each
- `auto` — classifier gate makes the decision
- `off` — skip learning loop entirely

Plus `--dry-run-learning` to preview without writing, and
`--auto-apply-learning` for CI runs.

---

## Skill system

Each agent has skill files under `skills/<agent>/<category>/<skill>.md`,
organised on 3 axes:

- **Language** — `dev/dart/`, `dev/typescript/`, `dev/python/`, `dev/kotlin/`,
  same for `techlead/`. Drop a `.md` file in any subfolder — `list_skills()`
  scans recursively.
- **Scope** — `simple_feature` / `feature_module` / `full_product`
- **Domain / phase / type** — `domain/ecommerce`, `phase/discovery`,
  `task/bug_fix`, `routing/code_feature`, etc.

Total: 38 skills across 6 agents (BA 8, Dev 8, TechLead 8, Test 5,
Design 3, PM 12).

### Skills as menu — LLM self-pick

System prompt for each agent contains a `WORKING MODES` menu listing
every available skill (frontmatter + 280-char summary). The LLM reads
the menu, picks one via `MODE: <skill_key>` on the first reply line.
No separate Python picker call — saves ~800 tokens per agent per session.

```
You are Developer.
[base rule]
---
## WORKING MODES
### `cubit_feature`
- scope: feature
- triggers: cubit, bloc, state management
[280-char summary…]
### `widget_only`
[…]
---
[user task]
```

LLM emits `MODE: cubit_feature` → base_agent records into
`_skill_usage_log` with `method=llm_self`. If the LLM forgets the tag,
fallback keyword scorer picks best-match skill (`method=fallback_keyword`).

### Auto-create when no skill matches

When neither the menu nor the keyword fallback finds a match, base_agent
calls SkillDesigner to draft a fresh skill on the fly into
`skills/<agent>/auto/<slug>.md`. Default ON; opt out via
`MULTI_AGENT_AUTO_CREATE_SKILL=0`.

### Self-improving (cross-session)

After ≥ 4-5 uses, `learning.skill_runner` has enough outcome data to:

- **REFINE** mid-score skills (5-7 avg) — SkillDesigner rewrites shadow
- **CREATE** new skills for chronic misfit patterns
- **MERGE** near-duplicates (trigger overlap ≥ 70 %)
- **DEPRECATE** consistently underperforming skills (< 5 avg across ≥ 5 uses)

A/B promotion requires the shadow to beat its parent by ≥ 0.5 score
margin across ≥ 2 sessions.

---

## User feedback

```bash
mag feedback <session_id> --rating 4 \
    --comment "login works but password reset email never arrives"
```

Writes to `rules/<profile>/.feedback/<session_id>.jsonl`. On the next
session, the evolver picks this up as a **high-trust signal** (weight
1.0 vs 0.6 for LLM) and surfaces it for review. Your feedback is the
single strongest input.

---

## CLI commands

| Command | What it does |
|---------|-------------|
| `python main.py "idea"` | Run full pipeline on a new request |
| `python main.py --resume <id>` | Continue a session from checkpoint |
| `python main.py --update <id> "change"` | Amend a finished session |
| `python main.py --feedback <id>` | Replay with runtime feedback injected |
| `python main.py --list` | List resumable sessions |
| `python main.py --profiles` | List available rule profiles |
| `python main.py --doctor` | Verify environment (Python, claude CLI, git…) |
| `python main.py --check-arch` | Run architecture compliance audit (§12 rules) |
| `python main.py status` | Health snapshot (shadow tests, reputation, cost trend) |
| `python main.py validate-rubric` | Pearson correlation between critic score and real outcomes |
| `python main.py rubric-classifier` | Training status + feature weights of P(regress) gate |
| `python main.py undo <id>` | Revert rule changes from a specific session |
| `python main.py --trend` | Cross-session score trends per agent |
| `mag feedback <id> --rating N --comment "…"` | Structured post-run feedback |

Short flags:

- `--dry-run-learning` — preview rule changes, don't apply
- `--auto-apply-learning` — CI mode, no prompts
- `--budget 800000` — override token budget
- `--reselect-plan` — re-pick Claude subscription plan

---

## Environment variables

All variables go through `core.config` (no stray `os.environ.get`
anywhere in the codebase — enforced by `scripts/check_architecture.py`).

| Env var | Default | Effect |
|---------|---------|--------|
| `MULTI_AGENT_LEARNING_MODE` | `propose` | `propose` \| `auto` \| `off` |
| `MULTI_AGENT_LEARNING_DRY_RUN` | `0` | `1` = preview only |
| `MULTI_AGENT_SHADOW_AB_FORCE` | `0` | Bypass ≥ 30-session gate |
| `MULTI_AGENT_SKILL_HEURISTIC` | `0` | `1` = opt-out, dùng keyword scorer (default: LLM picks via MODE: tag) |
| `MULTI_AGENT_SKILL_MAX` | `2` | Max active skills per agent (1-3) |
| `MULTI_AGENT_PM_AUTO_CONFIRM` | `0` | `1` = skip PM `[Y/n]` plan-confirm gate (CI mode) |
| `MULTI_AGENT_AUTO_CREATE_SKILL` | `1` | `0` = disable SkillDesigner auto-draft when no match |
| `MULTI_AGENT_AUTO_COMMIT` | `1` | Auto-commit Dev output to branch |
| `MULTI_AGENT_MAX_CONCURRENT` | `3` | Cap concurrent Claude calls (1-16) |
| `MULTI_AGENT_CALL_SPACING_MS` | `100` | Minimum gap between call starts |
| `MULTI_AGENT_CRITIC_ALL` | `0` | `1` = force Critic on every step |
| `MULTI_AGENT_SKILL_REVIEW` | `0` | `1` = manual review before skill create/merge |
| `MULTI_AGENT_RULE_CONFIRM` | `0` | `1` = extra confirm in evolver apply |
| `MULTI_AGENT_LEGACY_RULE_OPTIMIZER` | `0` | Fallback to old classifier loop |
| `MULTI_AGENT_TL_CRITIC_ALWAYS/_NEVER` | `0` | Force Critic on/off for TechLead |
| `MULTI_AGENT_NO_AUTO_FEEDBACK` | `0` | Skip auto-feedback collection |
| `MULTI_AGENT_AUTO_HEAL` | `1` | Auto-heal broken health check |
| `MULTI_AGENT_DEBUG` | `0` | Verbose error logging |

---

## Architecture

The project follows a strict layered architecture enforced by
`scripts/check_architecture.py` (runs in CI):

```
main.py → orchestrator.py (thin assembler, 766 lines)
           ↓
pipeline/ → agents/ → core/
    ↓        ↓
analyzer/ ← learning/ ← context/
```

**Package responsibilities:**

| Package | Contains |
|---------|----------|
| `core/` | Pure utilities: paths, logging, config, exceptions, text, message bus, tokens |
| `session/` | Session ID, checkpoint I/O, conversation export |
| `pipeline/` | Flow: task-based runner, critic loop, PM router, critic gating, clarification |
| `analyzer/` | **Measure + predict:** outcome logger, score adjuster, classifier, cost history |
| `learning/` | **Propose changes:** rule/skill runners, evolver, integrity, revise history |
| `context/` | Project detection, maintain mode, git, file scanning |
| `agents/` | One file per agent role; each subclasses `BaseAgent` |
| `testing/` | Real test runners (Patrol, Maestro) + code output savers |
| `reporting/` | HTML per-session + cross-session trend reports |
| `scripts/` | Dev tooling (architecture checker, migrations) |
| `tests/` | 92 pytest tests |

**Cross-cutting — single source of truth:**

- All paths via `core.paths.RULES_DIR`, `learning_dir(profile)`, etc.
- All logging via `core.logging.tprint` (thread-safe)
- All env vars via `core.config.get_bool`, `get_int`, `get_learning_mode`
- Custom exceptions via `core.exceptions.AgentCallError`, `CheckpointCorrupt`, …

See `CLAUDE.md` § 12 for the full rule list and documented exceptions.

**Refactor journey:**

- **orchestrator.py:** 3,439 → 766 lines (−78 %) across 10 phases
- **Tests:** 0 → 92 pytest tests
- **Modules added:** core/paths, core/logging, core/config, core/exceptions,
  pipeline/task_based_runner, pipeline/critic_loop, pipeline/critic_gating,
  pipeline/clarification, pipeline/pm_router, pipeline/session_runner,
  analyzer/outcome_pipeline, analyzer/outcome_logger, analyzer/regression_classifier,
  learning/rule_runner, learning/skill_runner, learning/trends,
  learning/shadow_runner, session/conversation_export, context/maintain_detector,
  testing/runners, scripts/check_architecture

---

## Testing

```bash
# All 92 tests
python -m pytest tests/ -v

# Architecture compliance only
python -m pytest tests/test_architecture.py

# Mock claude CLI for unit tests — no real LLM calls
# See tests/conftest.py for fixtures
```

Test coverage:

- **Pure functions** — parsers, score_adjuster math, outcome_logger
  Pearson, regression classifier fit/predict
- **Data classes** — session manager checkpoint, message bus recent()
- **Flow** — critic_loop PASS/REVISE paths, PM fast-path gate
- **Architecture** — boundaries, file sizes, cross-cutting violations

The architecture test (`test_architecture.py`) runs
`scripts/check_architecture.py` and fails CI on any violation — paths
not via `core.paths`, local `tprint` definitions, raw env vars, or
orchestrator exceeding 800 lines.

---

## Contributing

Before opening a PR:

```bash
python main.py --check-arch   # 0 violations
python -m pytest tests/        # all pass
python main.py --doctor        # environment OK
```

See `CLAUDE.md` for conventions (§3) and architecture rules (§12).

**Common pitfalls already fixed — don't re-introduce:**

- `import re` inside a function → hoist to top
- `Path(__file__).parent.parent / "rules"` → use `core.paths.RULES_DIR`
- `os.environ.get("MULTI_AGENT_*")` → use `core.config.get_bool/get_int`
- `except Exception: pass` → narrow to specific types
- Local `tprint` / `_tprint` → import from `core.logging`
- `session_id.split("_", 2)` → suffix-match with `STEP_KEYS`
- Compound score subtraction → use `ScoreAdjuster` blended sum
- `from __future__ import annotations` must be the first import statement

---

## When to use / when not to

**Use when:**

- Solo developer on a mobile / web product with ≥ 5 features to ship
- Codebase large enough that grep-ing is painful
- Willing to review generated code (it's Claude, not magic)
- Want a traceable audit log of every LLM decision

**Don't use when:**

- < 100 LOC task — overhead > benefit
- No test baseline — pipeline can't detect regressions
- Non-mobile + non-web stack without Patrol/Maestro — test-outcome
  signals won't work
- Zero budget for Claude API / subscription

---

## License

MIT — see `LICENSE`.
