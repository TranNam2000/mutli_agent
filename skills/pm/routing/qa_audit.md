---
SCOPE: investigation
TRIGGERS: rà soát, ra soat, audit, kiểm tra tài liệu, kiem tra tai lieu, review doc, review docs, review documentation, đánh giá, danh gia, verify spec, inspect, check codebase, code review, security review, compliance check
STEPS: investigation
AGENTS: Investigator
RESPONSE_FORMAT: findings_report
MAX_TOKENS: 1500
---

# PM Skill — Audit / Doc Review

User muốn **đánh giá** code/spec/doc đã có. **Read-only** — không sửa.

## Routing
- **STEPS: investigation**  — single step
- Investigator agent đọc codebase / docs → produce findings report
- KHÔNG gọi BA/Dev/Test (không tạo gì mới)

## Trả lời user
Format report:
1. **Scope** — đã review những gì (file/folder/spec)
2. **Findings** — list issue, mỗi item: severity (critical/high/med/low) + location + recommendation
3. **Strengths** — chỗ đã tốt, không cần đụng
4. **Open questions** — câu hỏi cho user/team trước khi action
5. **Suggested next step** — nếu user muốn fix → kind nào (bug_fix/refactor/feature)

## KHÔNG
- Đừng đề xuất sửa code trong report — chỉ findings + recommendation
- Đừng include test step — không có code change
