---
SCOPE: bug_fix
TRIGGERS: fix, bug, crash, regression, broken, lỗi, sửa lỗi, sua loi, khắc phục, hotfix, error in, doesn't work, exception, stack trace, null pointer
STEPS: dev, test
AGENTS: Dev → QA
RESPONSE_FORMAT: bug_diff
MAX_TOKENS: 1200
---

# PM Skill — Bug Fix

User báo bug — code đã có, fix chỗ sai, không build mới.

## Routing
- **STEPS: dev, test**  — bỏ BA/Design/TechLead
- Dev: identify root cause + patch
- QA: regression test (cover bug + adjacent paths)

## Trả lời user
1. **Root cause** — 1-2 câu giải thích nguyên nhân thật
2. **Files patched** — danh sách file + số dòng đổi
3. **Test added** — test case mới cho bug này (regression guard)
4. **Verify** — câu lệnh user chạy để confirm: `flutter test`, `pytest`, ...

## KHÔNG
- Đừng pull BA vào — yêu cầu rõ rồi
- Đừng yêu cầu redesign — chỉ fix
