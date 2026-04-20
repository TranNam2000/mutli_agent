# Multi-Agent Product Development Pipeline

A task-based AI pipeline that turns a product idea or bug report into classified tasks, prioritizes them by resource/business value, implements them, and verifies the output — all via specialized Claude agents with self-improving rules and skills.

## Flow

```
Input (task / feature / bug / URL)
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

## Environment variables (optional)

```bash
export MULTI_AGENT_FLOW=task-based         # pipeline variant
export MULTI_AGENT_MAX_CONCURRENT=3        # parallel Claude calls
export MULTI_AGENT_CALL_SPACING_MS=100     # min ms between calls
export MULTI_AGENT_AUTO_COMMIT=1           # auto-commit Dev step
export MULTI_AGENT_NO_AUTO_FEEDBACK=0      # skip post-build Maestro/logcat
export MULTI_AGENT_AUTO_HEAL=1             # auto re-run on BLOCKERs
export MULTI_AGENT_SKILL_REVIEW=0          # 1 = prompt before writing new skills
```

## License

MIT
