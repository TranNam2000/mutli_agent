# Code Review Feedback — Multi-Agent Product Development Pipeline

> Người review: Claude Code (đọc trực tiếp từng file)
> Ngày: 2026-04-21
> Mục đích: Góp ý kiến trúc và chất lượng code — không phải chỉ trích, mà để cùng cải thiện.

---

## Tổng quan

Pipeline có kiến trúc rõ ràng: PM router → BA → Design → BA consolidate → TechLead → (Dev ║ QA test plan) → QA review, với learning loop tự cải thiện qua nhiều session. Các module như `ScoreAdjuster`, `SkillOptimizer`, `IntegrityRules` cho thấy tư duy hệ thống nghiêm túc. Đây không phải vibe coding.


---

## 🔴 Lỗi nghiêm trọng (cần fix ngay)

### 1. `NameError` làm crash khi chạm quota — `forice` không tồn tại

**File:** `orchestrator.py:432`

```python
choice = input("  Continue? [C]ontinue / [S]top & save: ").strip().upper()
if forice in ("C", "S", ""):   # NameError: 'forice' is not defined
    break
```

Biến `choice` được define ở dòng 431 nhưng check `forice` ở dòng 432. Pipeline crash mỗi khi user dùng ≥95% token quota. Fix 2 ký tự:

```python
if choice in ("C", "S", ""):
```

---

### 2. Path traversal — LLM có thể đọc file tùy ý trên máy

**File:** `agents/investigation_agent.py:145–154`

`_read_files()` nhận path từ LLM output rồi đọc file đó **không có bất kỳ validation nào**:

```python
abs_p = Path(raw_path)
if abs_p.exists() and abs_p.is_file():
    content = abs_p.read_text(encoding="utf-8", errors="ignore")
```

Nếu LLM trả về `FILE: /etc/passwd` hoặc `FILE: ~/.ssh/id_rsa`, pipeline sẽ đọc và đưa nội dung đó vào prompt tiếp theo. Fix:

```python
def _safe_read(path: Path, base_dir: Path) -> str | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(base_dir.resolve())  # ValueError nếu ra ngoài
        return resolved.read_text(encoding="utf-8", errors="ignore")
    except (ValueError, OSError):
        return None
```

---

## 🟠 Vấn đề kiến trúc lớn

### 3. Dev agent hardcode Flutter — multi-stack chỉ là "trên giấy"

**File:** `agents/dev_agent.py:87`

```python
"Implement fully with Flutter/Dart following Clean Architecture."
```

Dù `project_detector.py` detect được Flutter, Node, Python, Rust, Go, Java — Dev agent luôn nhận instruction Flutter. Project Node.js sẽ nhận instruction Flutter và sinh code sai mà không có warning.

**Góp ý:** Inject `project_info.kind` từ orchestrator vào system prompt của Dev, hoặc nói thẳng trong README là "chỉ support Flutter hiện tại". Ẩn limitation nguy hiểm hơn nói thẳng.

---

### 4. Learning loop chạy hoàn toàn tự động — không có bước review của người

Với `MULTI_AGENT_AUTO_COMMIT=1` (default), pipeline tự rewrite `rules/*.md` sau đủ lần REVISE mà không hỏi user. `ReviseHistory.should_auto_apply()` trigger sau `AUTO_THRESHOLD = 5` lần — không có gate nào.

**Góp ý:** Thêm confirmation trước khi apply:

```
Tìm thấy 2 rule changes từ session này:
  1. BA: thêm "luôn có acceptance criteria cho mỗi task"
  2. Dev: thêm "phải có error handling cho mọi API call"

Apply những thay đổi này? [Y/n]:
```

Hoặc ít nhất flag `--dry-run-learning` để xem sẽ thay đổi gì mà không apply.

---

### 5. Score đi qua 4 lớp transform — không thể debug kết quả

Score của một agent đi qua: `Critic raw → scope reweight → test outcomes → downstream signals → cost penalty`. `ScoreAdjuster` lưu `score_adjustment` dạng string nhưng không có chỗ nào in breakdown tổng hợp ra terminal cho user thấy.

**Góp ý:** In "hóa đơn điểm" cuối session:

```
Dev score:  8  (critic raw)
  − 3.0    Patrol tests failed 30%
  − 2.0    2 MISSING_INFO leaked downstream
  ─────────────────────────────────────
  = 3      final
```

Dữ liệu đã có trong `ScoreAdjuster.adjustments` — chỉ cần thêm display function.

---

### 6. `orchestrator.py` — 3,409 dòng làm quá nhiều việc

Một class handle đồng thời: session I/O, git integration, agent wiring, token accounting, user prompts, health checks, report generation, skill optimization, rule optimization, parallel execution, checkpoint/resume. Rất khó test từng phần, khó thêm pipeline variant mới.

**Góp ý:** Tách tối thiểu 3 class:
- `SessionManager` — session ID, checkpoint, git branch
- `PipelineRunner` — agent orchestration, message passing
- `PostRunAnalyzer` — scoring, learning, reporting

Chỉ tách `PostRunAnalyzer` là dọn được ~400–600 dòng.

---

## 🟡 Vấn đề code quality

### 7. "You is" — sai ngữ pháp tại 10 chỗ trong agents

Tìm trong code thực tế:

| File | Dòng | Code thực tế |
|------|------|--------------|
| `agents/dev_agent.py` | 29 | `"You is {self.ROLE}. Add missing widget Key..."` |
| `agents/dev_agent.py` | 59 | `"You is {self.ROLE}. Ask a concise question..."` |
| `agents/techlead_agent.py` | 329 | `"You is Tech Lead review task list from BA..."` |
| `agents/techlead_agent.py` | 382 | `"You is {self.ROLE}. Build a concise, actionable..."` |
| `agents/techlead_agent.py` | 403 | `"You is {self.ROLE}. Triage bugs from QA..."` |
| `agents/test_agent.py` | 81 | `"You is {self.ROLE}. Ask a concise question..."` |
| `agents/test_agent.py` | 128 | `"You is {self.ROLE}. Concise question (max 80 words)..."` |
| `agents/design_agent.py` | 175 | `"You is {self.ROLE}. Ask a concise question..."` |
| `agents/design_agent.py` | 305 | `"You is UI/UX Designer. Tóm tắt design specs..."` |
| `agents/design_agent.py` | 341 | `"You is UI/UX Designer. Cải tcurrent Stitch prompt..."` |
| `agents/investigation_agent.py` | 7 | `"You is a Senior Code Investigator chuyên đọc and analyze..."` |
| `learning/skill_selector.py` | 192 | `"You is {agent_key} agent. Choose skill phù hợp nhất..."` |

Prompt quality ảnh hưởng trực tiếp đến output quality. Fix nhanh: `sed -i 's/You is /You are /g'` trên toàn bộ agents/.

---

### 8. Typo trong code và prompt gây lỗi hiểu

Các typo thực tế tìm thấy khi đọc code:

| File | Dòng | Typo | Đúng phải là |
|------|------|------|-------------|
| `agents/design_agent.py` | 341 | `"Cải tcurrent Stitch prompt"` | `"Improve current Stitch prompt"` |
| `core/plan_detector.py` | 127 | `"--reselect-plan to tor đổi"` | `"thay đổi"` |
| `core/plan_detector.py` | 8 | `"Cached forice in ~/.claude_pipeline.json"` | `"choice"` |
| `learning/revise_history.py` | 91 | `sin_patterns = [...]` | `consistent_patterns` |
| `learning/revise_history.py` | 105 | `"Return sin PASS patterns"` | `"consistent"` |
| `agents/ba_agent.py` | 36–37 | `"question cụ can"` (×3) | `"cụ thể"` |

---

### 9. Mixed language trong structured fields — parser sẽ đọc sai

**File:** `agents/ba_agent.py:99–104`

```python
"REASON_ba: [short reason | N/A]"
"REASON_pm: [lý do | N/A]"
"REASON_design: [lý do | N/A]"
```

Parser ở `_parse_impact()` dùng regex đọc field `REASON_ba`, `REASON_pm`... nhưng một số field hướng dẫn bằng tiếng Việt `[lý do]`, số khác tiếng Anh. LLM nhận instruction không nhất quán sẽ trả về output không nhất quán, làm parser fail silently.

Tương tự trong `agents/ba_agent.py:229`:
```python
"=== TÀI LIỆU HIỆN CÓ ==="  # trong prompt mostly tiếng Anh
```

**Góp ý:** Chọn một ngôn ngữ cho toàn bộ structured fields. Conversation với user có thể bilingual, nhưng format spec phải nhất quán.

---

### 10. `import re as _re` bên trong function — 11 nơi

```
orchestrator.py:1090, 2298, 2992
agents/design_agent.py:103, 138–139  (dòng 138: __import__("re") anti-pattern)
agents/techlead_agent.py:209, 244, 348
agents/ba_agent.py:340
learning/rule_evolver.py:102, 547
learning/skill_selector.py:266
```

Import bên trong function không sai nhưng gây khó đọc — người đọc phải scroll xuống mới thấy dependency. Đặc biệt `design_agent.py:138–139` dùng `__import__("re")` không có lý do gì:

```python
name_m = __import__("re").search(r"COMPONENT_NAME:\s*(.+)", raw)
ext_m  = __import__("re").search(r"EXTENSION_NOTES:\s*(.+)", raw)
```

---

### 11. `system_prompt` property đọc disk mỗi lần gọi

**File:** `agents/base_agent.py:89–101`

```python
@property
def system_prompt(self) -> str:
    base = self._load_effective_rule()   # đọc file từ disk
    if self._active_skills:
        from learning.skill_selector import render_skills
        return render_skills(self._active_skills, base)
    return base
```

Mỗi lần `_call()` invoke `self.system_prompt`, nó đọc file từ disk. Trong một session dài với nhiều critic revision rounds, cùng file được đọc hàng chục lần. Rules không thay đổi giữa chừng một session — cache sau lần đọc đầu là đủ.

---

### 12. `promote_shadow` ghi file rồi rename — không atomic

**File:** `learning/skill_optimizer.py:415–417`

```python
parent_path.write_text(shadow_path.read_text(encoding="utf-8"), encoding="utf-8")
shadow_path.rename(shadow_path.with_suffix(".retired.md"))
```

Nếu process crash sau `write_text` nhưng trước `rename`: `parent_path` đã bị ghi đè với nội dung mới, `shadow_path` vẫn tồn tại — skill state corrupt không recoverable.

Fix atomic:
```python
tmp = parent_path.with_suffix(".tmp")
tmp.write_text(shadow_path.read_text(encoding="utf-8"), encoding="utf-8")
tmp.replace(parent_path)  # atomic trên cùng filesystem
shadow_path.rename(shadow_path.with_suffix(".retired.md"))
```

---

### 13. Duplicate keywords trong `_SCOPE_KEYWORDS`

**File:** `learning/skill_selector.py:26`

```python
"bug_fix": ["fix", "bug", "fix", "bug", "crash", ...]
```

`"fix"` và `"bug"` xuất hiện 2 lần. Không crash nhưng lệch điểm khi scoring — tasks chứa "fix" hoặc "bug" bị score cao hơn thực tế vì count trùng.

---

### 14. Không có test nào cho learning modules

Toàn bộ learning loop — `score_adjuster`, `skill_optimizer`, `revise_history`, `integrity_rules` — không có một unit test nào. Đây là code có impact lớn nhất (điều khiển AI tự học), nhưng cũng là code dễ test nhất vì là pure functions:

```python
def test_score_adjuster_patrol_penalty():
    reviews = [{"agent_key": "dev", "score": 8}]
    result = adjuster.apply_test_outcomes(reviews, patrol_fail_rate=0.30)
    assert result[0]["score"] == 5  # 8 − 3.0

def test_promote_shadow_atomic():
    # Verify parent_path updated even if interrupted
    ...

def test_detect_regression():
    # scores before: [7, 7.5, 8], after: [5, 4.5]
    assert history.detect_regression("ba", apply_session_id="s004") == True
```

---

### 15. Các vấn đề nhỏ khác

| Vấn đề | File | Mức độ |
|--------|------|--------|
| `base_agent.py:316` — `respond_to` pass `f"{from_role} hỏi: {question}"` — tiếng Việt trong internal API | `base_agent.py` | Low |
| `_call_with_retry` hardcode `timeout=600` — không configurable | `base_agent.py:228` | Low |
| `__import__("threading").Lock()` và `__import__("os").environ` dùng thay vì import đầu file | `orchestrator.py:1409, 1425` | Low |
| `print_stitch_review` hiển thị `score_accessibility` và `score_flow` nhưng `_parse_review` không parse 2 field này — luôn fallback về `review["score"]` | `design_agent.py:363–373` | Medium |
| `_try_cli_plan()` parse JSON từ `claude auth status` — nhưng CLI có thể không trả JSON, sẽ crash silently trong `except Exception: pass` | `plan_detector.py:46–47` | Low |

---

## Tóm tắt ưu tiên

| # | Góp ý | Impact | Effort |
|---|-------|--------|--------|
| 1 | Fix `forice` → `choice` NameError | 🔴 Critical | Trivial |
| 2 | Path traversal guard trong `_read_files` | 🔴 Critical | Nhỏ |
| 3 | Fix "You is" → "You are" (10 chỗ) | 🟠 Cao | Trivial |
| 4 | Fix typo: `tcurrent`, `tor đổi`, `sin_patterns`, `cụ can` | 🟠 Cao | Trivial |
| 5 | Fix `promote_shadow` atomic write | 🟠 Cao | Nhỏ |
| 6 | Gate review trước khi apply learning changes | 🟠 Cao | Nhỏ |
| 7 | Tests cho `score_adjuster`, `revise_history`, `integrity_rules` | 🟠 Cao | Vừa |
| 8 | Fix duplicate keywords trong `_SCOPE_KEYWORDS` | 🟡 Vừa | Trivial |
| 9 | Nhất quán ngôn ngữ trong structured prompt fields | 🟡 Vừa | Vừa |
| 10 | Score breakdown in rõ cuối session | 🟡 Vừa | Nhỏ |
| 11 | Cache `system_prompt` property | 🟡 Vừa | Nhỏ |
| 12 | Dev agent inject stack từ project_detector | 🟠 Cao | Vừa |
| 13 | Fix `print_stitch_review` hiển thị field không tồn tại | 🟡 Vừa | Nhỏ |
| 14 | Tách `orchestrator.py` thành 3 class | 🟡 Vừa | Lớn |

---

## Kết luận

Project có ý tưởng hay và thiết kế tổng thể tốt. `ScoreAdjuster` blending real test outcomes, `IntegrityRules` tự học từ audit log, `SkillOptimizer` với shadow A/B — đây là tư duy hệ thống rõ ràng.

**Ưu tiên ngay:**
1. Fix `forice` NameError và path traversal — 2 bug blocking production
2. Fix tất cả typo (trivial, chỉ mất 10 phút): "You is", "tcurrent", "tor đổi", `sin_patterns`
3. Fix `promote_shadow` atomic write — silent data corruption khi crash

**Sau đó:** Thêm confirmation gate cho learning loop và score breakdown display — đây là 2 thay đổi nhỏ nhưng sẽ khiến toàn bộ hệ thống đáng tin hơn nhiều và dễ debug hơn.
