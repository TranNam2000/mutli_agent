# CLAUDE.md — Multi-Agent Pipeline Coding Guide

Hướng dẫn cho Claude sessions làm việc trong codebase này. **Đọc §3 (Conventions) và §12 (Architecture Rules) trước khi sửa bất cứ gì.**

Chạy `python main.py --check-arch` bất kỳ lúc nào để kiểm tra code có còn tuân thủ architecture không — violation = CI fail.

---

## 1. Project là gì

Python CLI đa agent cho pipeline phát triển sản phẩm:

```
PM router → BA → Design → TechLead → (Dev ∥ Test)
```

Mỗi agent là subclass của `BaseAgent`, gọi `claude` CLI qua subprocess. Hệ thống có learning loop tự cải thiện rules + skills sau mỗi session, với classifier dự đoán `P(regress)` để gate việc apply rule.

**Entry point:** `main.py` → `ProductDevelopmentOrchestrator` (định nghĩa trong `orchestrator.py`, 766 dòng, thin assembler).

**Tests:** 48 pytest tests cover pure functions + flow. Chạy `python -m pytest tests/`.

---

## 2. Package layout (sau refactor hoàn chỉnh)

```
multi_agent/
├── main.py                       # CLI entry, 523 dòng
├── orchestrator.py               # ProductDevelopmentOrchestrator — 766 dòng (thin assembler)
│
├── core/                         # Cross-cutting utilities (no project dependencies)
│   ├── paths.py                  # RULES_DIR, SKILLS_DIR, OUTPUTS_DIR, learning_dir()
│   ├── logging.py                # tprint() — thread-safe print
│   ├── config.py                 # get_bool/get_int/get_learning_mode for MULTI_AGENT_*
│   ├── exceptions.py             # AgentCallError, CheckpointCorrupt, LearningDataSparse, ...
│   ├── text_utils.py             # smart_trim, extract_section (pure)
│   ├── message_bus.py            # MessageBus.send/reply/recent()
│   ├── token_tracker.py          # TokenTracker — per-agent budget
│   ├── plan_detector.py          # Claude subscription plan detection
│   ├── doctor.py                 # Environment check
│   └── ux.py                     # **Exception**: CLI aggregator — imports across packages
│
├── session/                      # Session identity + checkpoint I/O
│   ├── session_manager.py        # SessionManager: session_id, save, load, list_sessions
│   └── conversation_export.py    # .md + .json session export with full metadata
│
├── pipeline/                     # Pipeline flow + data types
│   ├── task_based_runner.py      # run_task_based_pipeline, qa_dev_loop, spec_postmortem
│   ├── task_models.py            # Task, Priority, Complexity, Risk, SprintPlan (dataclasses)
│   ├── skill_selector.py         # select_skills, render_skills (routing)
│   ├── critic_gating.py          # critic_enabled_for, trigger_emergency_audit, audit log
│   ├── clarification.py          # clarification_gate, batch_clarify, pm_clarify_with_user
│   ├── session_runner.py         # run_update, run_feedback, run_resume
│   ├── critic_loop.py            # run_with_review, review_only, escalate
│   ├── pm_router.py              # run_pm_router, run_investigation_path
│   └── parsers.py                # extract_blockers/missing_keys/fixes/info (pure)
│
├── analyzer/                     # Measure + render + predict (no rule/skill proposals)
│   ├── outcome_pipeline.py       # analyze_session — bridge finished session → learning data
│   ├── outcome_logger.py         # score_correlation.jsonl append + Pearson correlation
│   ├── skill_outcome_logger.py   # skill_outcomes.jsonl per (agent, skill, session)
│   ├── regression_classifier.py  # Pure-Python logistic regression (P(regress) gate)
│   ├── score_adjuster.py         # Blended weighted sum + MISSING_INFO attribution
│   ├── score_renderer.py         # print_score_breakdown (final score "hoá đơn")
│   ├── cost_history.py           # Per-(agent, scope, complexity) token budgets + history
│   └── session_report.py         # collect_auto_feedback, write_html_report
│
├── learning/                     # Propose rule/skill edits (writes to rules/, skills/)
│   ├── rule_runner.py            # run_rule_optimizer + regression rollback + criteria upgrade
│   ├── skill_runner.py           # run_skill_optimizer + apply_refine/create/merge
│   ├── trends.py                 # print_score_trends (sparkline)
│   ├── runners.py                # Back-compat shim (re-exports from rule/skill/trends)
│   ├── rule_evolver.py           # 4-source consensus (LLM + integrity + user + cost)
│   ├── skill_optimizer.py        # SkillOptimizer + SkillHistory (shadow A/B)
│   ├── revise_history.py         # REVISE pattern tracker, 90d blacklist decay
│   ├── integrity_rules.py        # Module blacklist, keyword risk, agent reputation
│   ├── shadow_runner.py          # activate_rule_variants, log_shadow_rule_scores
│   ├── audit_log.py              # AuditLog for false-negatives
│   └── task_metadata.py          # TaskMetadata dataclass
│
├── context/                      # Project detection, git, file scanning
│   ├── maintain_detector.py      # auto_detect_maintain, load_project_context
│   ├── project_detector.py       # Flutter/Node/Python/Rust/Go/Java detection
│   ├── project_context_reader.py # Scan project → context markdown
│   ├── scoped_reader.py          # build_scoped_context
│   ├── context_builder.py        # for_design_from_ba, for_dev_from_techlead, etc.
│   ├── health_check.py           # Pre-flight compile + test baseline
│   ├── git_helper.py             # Auto branch, commit step
│   ├── output_paths.py           # session_file layout
│   └── refresh.py                # Context reload throttling
│
├── testing/                      # Real test runners + code output savers
│   ├── runners.py                # save_implementation_files, run_patrol_tests, run_maestro_flows
│   ├── patrol_runner.py          # Flutter integration
│   ├── maestro_runner.py         # Mobile E2E
│   ├── stitch_browser.py         # Stitch AI UI generation (browser automation)
│   ├── flutter_runner.py         # Flutter-specific helpers
│   └── auto_feedback.py          # Runtime feedback collection
│
├── agents/                       # LLM + output parsing per role
│   ├── base_agent.py             # BaseAgent._call → claude CLI + retry + AgentCallError
│   ├── pm_agent.py               # PM router: classify kind, heuristic-first fast path
│   ├── ba_agent.py               # BA: clarify + task list
│   ├── design_agent.py           # UI/UX specs
│   ├── techlead_agent.py         # Architecture + sprint planning
│   ├── dev_agent.py              # Implementation — stack injected từ project_info
│   ├── test_agent.py             # QA: test plan + review
│   ├── critic_agent.py           # YES/PARTIAL/MISS checklist scorer
│   ├── investigation_agent.py    # Read codebase before Dev (maintain mode)
│   ├── rule_optimizer_agent.py   # **Meta-agent**: propose rules/*.md edits
│   └── skill_designer_agent.py   # **Meta-agent**: propose skills/*.md edits
│
├── reporting/
│   ├── html_report.py            # Per-session HTML
│   └── trend_report.py           # Cross-session trends
│
├── rules/<profile>/              # Rule markdown (one per agent)
│   ├── ba.md, design.md, ...     # System prompts
│   ├── criteria/<agent>.md       # Checklist rubric for Critic
│   ├── .learning/                # JSON/JSONL: blacklist, reputation, outcomes, classifier
│   └── backups/                  # Rule snapshots before each apply
│
├── skills/<agent>/<name>.md      # Skill files (module/feature/simple/...)
│
├── scripts/
│   └── check_architecture.py     # **Enforces §12 rules** — run via `mag --check-arch`
│
└── tests/                        # pytest, 48 tests
    ├── conftest.py               # Fixtures: tmp_profile, fake_reviews, mock LLM
    ├── test_parsers.py           # pipeline/parsers
    ├── test_score_adjuster.py    # analyzer/score_adjuster blended math
    ├── test_outcome_logger.py    # analyzer/outcome_logger + Pearson
    ├── test_regression_classifier.py  # Logistic regression
    ├── test_session_manager.py   # Checkpoint + resume + 2-part/3-part session_id
    ├── test_core_config.py       # get_bool/get_int/learning_mode
    ├── test_message_bus.py       # recent() filter
    ├── test_pm_fast_path.py      # Heuristic-only skip LLM
    ├── test_critic_loop.py       # run_with_review flow (mock Critic)
    ├── test_runners.py           # learning.rule_runner + back-compat
    └── test_architecture.py      # Run scripts/check_architecture in CI
```

---

## 3. Conventions BẮT BUỘC

### Import order
```python
"""Module docstring first."""
from __future__ import annotations     # LUÔN line đầu sau docstring
import stdlib_modules
from third_party import Thing
from core.paths import RULES_DIR       # core / then other project packages
```

**KHÔNG BAO GIỜ:**
- `import re` bên trong function (hoist lên top)
- `__import__("re").search(...)` — anti-pattern, dùng `import re` ở top
- `import X` trước `from __future__ import annotations` — SyntaxError

### Cross-cutting — chỉ 1 cách duy nhất

**Logging:**
```python
from core.logging import tprint
tprint("  ✅ Dev done")
# NEVER: print() trong production code, local _tprint
```

**Paths:**
```python
from core.paths import RULES_DIR, SKILLS_DIR, learning_dir
path = learning_dir("default") / "score_correlation.jsonl"
# NEVER: Path(__file__).parent.parent / "rules"
```

**Env vars:**
```python
from core.config import get_bool, get_int, get_learning_mode
if get_bool("MULTI_AGENT_AUTO_COMMIT"): ...
# NEVER: os.environ.get("MULTI_AGENT_*") — scripts/check_architecture sẽ fail
```

**Exceptions:**
```python
from core.exceptions import AgentCallError, CheckpointCorrupt
raise AgentCallError(role=self.ROLE, attempts=3, last_error=e)
# NEVER: except Exception: pass  — narrow to (OSError, json.JSONDecodeError), v.v.
```

### File I/O atomic
```python
tmp = path.with_suffix(".tmp")
tmp.write_text(content, encoding="utf-8")
tmp.replace(path)   # atomic rename trên cùng filesystem
```

### Path traversal
Khi đọc file path từ LLM output, **LUÔN** validate qua base_dir:
```python
resolved = path.resolve()
resolved.relative_to(base_dir.resolve())   # ValueError nếu escape
```

### Session ID format
`YYYYMMDD_HHMMSS_<4hex>` (3 parts). Khi parse filename `<session_id>_<step>.md`:
- **DÙNG** suffix match với `STEP_KEYS` (`SessionManager.list_sessions` đã fix)
- **KHÔNG** `stem.split("_", 2)` — phá session_id

### Prompt language consistency
Structured fields phải nhất quán (tất cả English hoặc tất cả Vietnamese, không mix):

✅ Đúng:
```python
"REASON_ba: [short reason | N/A]"
"REASON_pm: [short reason | N/A]"
```

❌ Sai:
```python
"REASON_ba: [short reason | N/A]"
"REASON_pm: [lý do | N/A]"   # mix → parser output inconsistent
```

### Agent instructions — grammar
- ✅ `f"You are {self.ROLE}. ..."` (có space)
- ❌ `f"You is {self.ROLE}. ..."` (sai ngữ pháp → prompt quality↓)
- ❌ `f"You are{self.ROLE}. ..."` (thiếu space)

---

## 4. Thêm agent mới

1. Tạo `agents/myagent.py`:
```python
from __future__ import annotations
from .base_agent import BaseAgent

class MyAgent(BaseAgent):
    ROLE     = "My Role"
    RULE_KEY = "myagent"       # → rules/<profile>/myagent.md
    SKILL_KEY = "myagent"      # → skills/myagent/*.md

    def my_method(self, task: str) -> str:
        return self._call(self.system_prompt, task)
```

2. Export trong `agents/__init__.py`.
3. Tạo `rules/default/myagent.md` (system prompt).
4. Nếu cần Critic, tạo `rules/default/criteria/myagent.md`:
```markdown
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.4 format=0.2 quality=0.4

## Completeness
- [ ] Item 1
## Format
- [ ] Item 2
## Quality
- [ ] Item 3
```

5. Đăng ký trong `ProductDevelopmentOrchestrator.__init__`:
```python
self.agents["myagent"] = MyAgent(profile=profile)
```

6. Thêm key vào `STEP_KEYS` (orchestrator.py, session_manager.py `DEFAULT_STEP_KEYS`).

7. Viết test ở `tests/test_myagent.py`.

---

## 5. Thêm skill mới

Tạo `skills/<agent>/<scope>.md`:
```markdown
---
SCOPE: simple, feature, module, full_app, bug_fix
TRIGGERS: keyword1, keyword2
MAX_TOKENS: 6000
---

# <Agent> Skill — <Name>

<instruction content>
```

`pipeline.skill_selector.select_skills` **mặc định dùng LLM** để pick skill phù hợp dựa trên task + metadata + frontmatter của mỗi skill. Opt-out về heuristic keyword scorer bằng `MULTI_AGENT_SKILL_HEURISTIC=1`. Sau ~30 session, `analyzer.skill_outcome_logger` có đủ data cho `learning.skill_runner` đánh giá promote/demote.

---

## 6. Learning system — env vars

| Env var | Default | Tác dụng |
|---------|---------|---------|
| `MULTI_AGENT_LEARNING_MODE` | `propose` | `propose` (user Y/n) \| `auto` (classifier gate) \| `off` |
| `MULTI_AGENT_LEARNING_DRY_RUN` | `0` | `1` = preview-only, không ghi file |
| `MULTI_AGENT_LEARNING_AUTO` | `0` | Legacy alias cho `MODE=auto` |
| `MULTI_AGENT_SHADOW_AB_FORCE` | `0` | Bỏ qua gate "≥30 session" cho shadow A/B |
| `MULTI_AGENT_SKILL_HEURISTIC` | `0` | `1` = opt-out, dùng keyword scorer (default: LLM chọn skill) |
| `MULTI_AGENT_SKILL_MAX` | `2` | Max số skills active/agent |
| `MULTI_AGENT_AUTO_COMMIT` | `1` | Auto git commit sau Dev step |
| `MULTI_AGENT_MAX_CONCURRENT` | `3` | Giới hạn concurrent claude CLI calls |
| `MULTI_AGENT_CALL_SPACING_MS` | `100` | Min space giữa 2 calls |
| `MULTI_AGENT_CRITIC_ALL` | `0` | `1` = force Critic cho mọi step |
| `MULTI_AGENT_SKILL_REVIEW` | `0` | `1` = manual review before apply skill create/merge |
| `MULTI_AGENT_RULE_CONFIRM` | `0` | `1` = extra confirm step in rule_evolver apply |
| `MULTI_AGENT_LEGACY_RULE_OPTIMIZER` | `0` | `1` = fallback to classifier loop thay vì RuleEvolver |
| `MULTI_AGENT_TL_CRITIC_ALWAYS` / `_NEVER` | `0` | Force critic on/off for TechLead |
| `MULTI_AGENT_NO_AUTO_FEEDBACK` | `0` | `1` = skip auto feedback collection |
| `MULTI_AGENT_AUTO_HEAL` | `1` | `1` = auto heal broken health check |
| `MULTI_AGENT_DEBUG` | `0` | `1` = verbose error logging |

### Data flow

```
Session → critic_reviews
  → analyzer/outcome_pipeline.analyze_session()
      ├─ score_adjuster (blended, attribution) → final scores
      ├─ outcome_logger.jsonl       (1 dòng/agent)
      └─ skill_outcome_logger.jsonl (1 dòng/agent/skill)
  → (user apply rule) → snapshot_features → apply_features.jsonl
  → (≥30 labelled) train_regression_classifier → regression_model.json
  → next session: classifier_should_apply() gate → apply/shadow/skip
```

### Thresholds

- `MIN_TRAINING_SAMPLES = 30` — dưới ngưỡng → fallback count threshold
- `APPLY_MAX_PROBA = 0.20` — `P(regress) < 0.20` → auto-apply
- `SHADOW_MAX_PROBA = 0.50` — `< 0.50` → shadow A/B
- `SHADOW_AB_MIN_TOTAL_SESSIONS = 30` — dưới → luôn baseline
- `BLACKLIST_DECAY_DAYS = 90` — blacklist tự unblock
- `AUTO_THRESHOLD = 5` — pattern xuất hiện ≥ 5 lần (fallback khi chưa có classifier)

---

## 7. Scoring system

### Critic scoring (per agent)
3 chiều: `completeness`, `format`, `quality` — weights từ `rules/<profile>/criteria/<agent>.md`. LLM chấm YES/PARTIAL/MISS mỗi item, code tính weighted sum.

### ScoreAdjuster (analyzer/score_adjuster.py)
1. **Scope reweight**: weights khác nhau cho `simple` / `feature` / `full_app` / `bug_fix`
2. **Test outcomes**: blended weighted-sum với `P(fail)` — **KHÔNG compound subtraction**
3. **Upstream attribution**: nếu Dev output có `MISSING_INFO: ... MUST_ASK: BA`, ~70% test-fail pressure chuyển về BA (ít nhất Dev giữ 30%)
4. **Downstream signals**: clarification count + MISSING_INFO leakage
5. **Cost penalty**: used/expected ratio

**KHÔNG BAO GIỜ** dùng compound subtraction. Test `test_score_adjuster.py::test_no_compound_subtraction_on_moderate_failure` enforce.

---

## 8. Test khi không có claude CLI / login

```bash
# pytest — 48 tests
python -m pytest tests/ -v

# Architecture compliance audit
python main.py --check-arch

# Syntax scan
python3 -c "
import ast
from pathlib import Path
for p in Path('.').glob('**/*.py'):
    if '__pycache__' in str(p) or '.egg-info' in str(p): continue
    ast.parse(p.read_text(encoding='utf-8'))
print('syntax OK')
"

# Import smoke
python3 -c "from orchestrator import ProductDevelopmentOrchestrator; print('OK')"

# CLI không cần LLM
python main.py --help
python main.py --doctor
python main.py --list
python main.py status
python main.py validate-rubric
python main.py rubric-classifier
python main.py --check-arch
```

### Mock LLM trong unit test
```python
def test_something(monkeypatch):
    def fake_call(self, system, prompt):
        return "CLEAR: YES"  # fixture
    monkeypatch.setattr("agents.base_agent.BaseAgent._call", fake_call)
```

Xem `tests/conftest.py` cho fixtures `tmp_profile`, `fake_reviews`, `mock_claude_call`.

---

## 9. Common pitfalls — ĐỪNG LẶP LẠI

| Bug | File / Issue | Fix |
|-----|-------------|-----|
| `NameError: forice` | orchestrator.py (đã fix) / plan_detector.py docstring | Lint biến trước commit |
| Path traversal | `investigation_agent._read_files` | `_safe_read` + `relative_to(base)` |
| "You is" grammar | Nhiều agents | LUÔN `f"You are {self.ROLE}..."` |
| Score crash về 1 | compound subtraction | Blended weighted sum (`ScoreAdjuster`) |
| `__import__("re")` | `design_agent.py` | `import re` top, `re.search(...)` |
| `system_prompt` đọc disk mỗi call | `base_agent.py` | `_rule_cache` dict + `invalidate_rule_cache()` |
| `promote_shadow` non-atomic | `skill_optimizer.py` | `.tmp` + `os.replace()` |
| Dev hardcode Flutter | `dev_agent.py:87` | Inject `project_info.kind` |
| `list_sessions` parse 2-part | `SessionManager` | Suffix match `_<STEP_KEY>.md` |
| Inline `import re` | Toàn codebase | Hoist top-level |
| Path scoping bug `main.py` | Inline `from pathlib import Path` shadow module-level | Chỉ import ở module level |
| `if no ...` regex match substring | Typo sweep script | Word boundary `\bno\b` |
| `from __future__` must be first | ba_agent.py, skill_designer_agent.py | Đặt TRƯỚC mọi import khác |
| Bare `except Exception: pass` | 22+ chỗ (đã narrow 13) | `(OSError, json.JSONDecodeError, ...)` |

---

## 10. Git workflow

- Branch: `refactor/<topic>`, `feature/<topic>`, `fix/<topic>`
- Auto branch từ `GitHelper` khi vào maintain mode — đừng sửa
- Dev step auto-commit khi `MULTI_AGENT_AUTO_COMMIT=1`
- Commit message:
  ```
  <scope>: <short>

  - bullet fix 1
  - bullet fix 2
  ```

### Pre-commit checklist
1. `python main.py --check-arch` — 0 violations
2. `python -m pytest tests/` — all pass
3. `python main.py --doctor` — environment OK
4. Không `except Exception: pass` mới

---

## 11. Quick reference — commands

```bash
# Chạy pipeline
python main.py "tạo feature OAuth login"
python main.py --resume <session_id>
python main.py --update <session_id> "thay đổi spec"
python main.py --feedback <session_id>

# Learning mode
MULTI_AGENT_LEARNING_MODE=auto python main.py "..."   # classifier gate
python main.py "..." --dry-run-learning                # preview rules
python main.py "..." --auto-apply-learning             # CI mode

# Inspect + enforce
python main.py --list
python main.py status
python main.py validate-rubric          # correlation critic vs outcomes
python main.py rubric-classifier        # P(regress) classifier status
python main.py --check-arch             # architecture audit
python main.py --profiles
python main.py --trend

# Undo
python main.py undo <session_id>

# Test
python -m pytest tests/ -v
python -m pytest tests/test_architecture.py    # architecture-only
```

---

## 12. Architecture Rules — BẮT BUỘC tuân theo

Invariants sau đã được viết thành `scripts/check_architecture.py` + `tests/test_architecture.py`. **Vi phạm = CI fail.**

### 12.1 Package boundaries

| Package | Trách nhiệm | Không được làm |
|---------|-------------|----------------|
| `agents/` | LLM call + output parsing cho 1 agent | Lưu state persistent, orchestrate pipeline |
| `core/` | Utility thuần (paths, logging, text, parsing) — **không import bất kỳ domain package nào** | Business logic, I/O JSON học |
| `cli/` | CLI workflow aggregator (`mag status/chat/undo`). Được phép import across packages vì đây là entry-point helper | Gọi LLM; lưu learning state |
| `session/` | session_id + checkpoint I/O | Run pipeline, gọi agent |
| `pipeline/` | Pipeline flow, critic loop, parsers | Lưu learning state |
| `analyzer/` | **Đo + render + predict** (loggers, classifier, score transforms, cost) | **Đề xuất** thay đổi rule/skill |
| `learning/` | **Đề xuất** thay đổi (rule_runner, skill_runner, revise_history, integrity) | Render, log session outcome |
| `context/` | Project detection, git, file scanning | LLM call, orchestrate |
| `testing/` | Real test runners (Patrol, Maestro) | Unit test (ở `tests/`) |
| `reporting/` | HTML/trend reports | Session state, LLM call |
| `scripts/` | Dev tooling (arch checker, migrations) | Runtime logic |

**Phân biệt `analyzer/` vs `learning/`:**
- Output là **con số / báo cáo / prediction** → `analyzer/`
- Output là **đề xuất sửa file .md** → `learning/`

### 12.2 Dependency direction

```
main.py → orchestrator.py (thin assembler, 766 dòng)
           ↓
pipeline/ → agents/ → core/
    ↓        ↓
analyzer/ ← learning/ ← context/
```

**Documented exceptions** (enforced by checker allowlist):
- `cli/ux.py` — CLI aggregator package (formerly `core/ux.py`); imports domain packages (by nature)
- `agents/rule_optimizer_agent.py`, `agents/skill_designer_agent.py` — meta-agents, import learning state
- `agents/design_agent.py` — imports `testing.stitch_browser` cho UI generation
- `pipeline/critic_gating.py` — imports `analyzer.score_adjuster` + `learning.audit_log` (infrastructure class imports)
- `learning/` — imports `analyzer.score_adjuster`, `pipeline.skill_selector`, `pipeline.task_models` (back-compat re-exports)

### 12.3 Thin orchestrator (ENFORCED)

- `orchestrator.py` ≤ **800 dòng** — hard limit, `test_architecture.py` fail nếu vượt
- `learning/runners.py` ≤ **500 dòng** — soft limit (đã split thành rule_runner/skill_runner/trends)

### 12.4 Cross-cutting (ENFORCED)

- **Logging:** chỉ `from core.logging import tprint` — KHÔNG `print()` trong `agents/`, `pipeline/`, `learning/`, `analyzer/`, `testing/` (parallel execution → interleave). Checker `§3 tprint over print` flag vi phạm.
- **Paths:** chỉ `from core.paths import RULES_DIR, ...`
- **Env vars:** chỉ `from core.config import get_bool, get_int`
- **Exceptions:** dùng `core.exceptions.AgentCallError` khi `_call` retry cạn
- **Stdlib imports:** chỉ đặt ở module top, NEVER inline `import re/json/os/sys` bên trong function. Checker `§3 hoist stdlib imports` fail ngay.
- **Atomic I/O:** mọi state file persistent phải ghi qua `core.io_utils.atomic_write_text(path, content)` — không tự viết `tmp + rename` ad-hoc.

### 12.5 State files

Mọi state persistent (JSON/JSONL) phải:
- Sống trong `rules/<profile>/.learning/`
- Atomic write qua `core.io_utils.atomic_write_text` (tmp + replace)
- Schema-versioned qua `core.state_version`: bump `CURRENT_<NAME>_VERSION` + đăng ký migration trong `_MIGRATIONS` khi đổi shape
- Document trong docstring module

### 12.6 Testability

Mỗi module mới phải pass 1 trong 2:
- **Pure function**: không I/O, test chỉ cần input/output
- **Injected dependencies**: qua tham số / fixture, không singleton

### Enforcement

Mỗi PR phải:
1. `python main.py --check-arch` → 0 violations
2. `python -m pytest tests/` → all pass (includes test_architecture)

---

## 13. Khi Claude được giao task code project này

1. **Đọc CLAUDE.md trước** (đang làm)
2. Check file liên quan qua `Read` / `Grep`
3. Follow convention §3
4. Nếu thay đổi learning/scoring logic, chạy smoke test §8 + test đã viết
5. Sau khi sửa, chạy:
   - `python main.py --check-arch`
   - `python -m pytest tests/`
   - `python main.py --doctor`
6. **KHÔNG** tự commit — đưa patch cho user review

Hỏi user nếu:
- Thay đổi public API (`run`, `run_resume`, `run_update`, `list_sessions`)
- Đổi checkpoint format (session resume break)
- Đổi cấu trúc rule/skill markdown (ảnh hưởng LLM)
- Thêm dependency (update `requirements.txt` + `doctor.py`)
- Thêm env var mới (update `core/config.py` defaults + §6 bảng)
- Thêm package mới (update §12.1 boundary rules + `scripts/check_architecture.py`)

---

## Refactor history (summary)

Từ **3,439 dòng god class** → **766 dòng thin assembler** qua 8 phase refactor:

| Phase | Mục tiêu | Kết quả |
|-------|---------|---------|
| 1-2 | Critical bugs + typos + JSON log + caching | -101 |
| 3 | analyzer + session + core/text_utils | -122 |
| 4 | critic_loop + task_based_runner + more | -122 |
| 5 | run_rule_optimizer + cost_signals | -468 |
| 6 | run_task_based_pipeline + qa_dev_loop | -435 |
| 7 | clarification + testing runners | -415 |
| 8 | critic_gating + pm_router + shadow | -413 |
| 9 | maintain_detector + session_runner + more | -466 |
| 10 | runners.py split + scripts/check_architecture | shim |
| **Total** | | **-78%** |

**Tests:** 0 → 48 pytest tests. CI enforces architecture rules automatically.
