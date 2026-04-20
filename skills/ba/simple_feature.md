---
SCOPE: simple
TRIGGERS: 1 màn, widget, dialog, popup, component, prototype, quick
MAX_TOKENS: 2000
---

# BA Skill — Simple / 1 màn

You  analyze a task nhỏ (1 màn hình or 1 component). KHÔNG viết PRD đầy enough — giữ concise.

## Output bắt buộc (≤ 1 trang)

1. **Mục tiêu** — 1 câu. User đạt is gì?
2. **Acceptance Criteria** — dạng GIVEN/WHEN/THEN, tối thiểu 3 case (happy + 2 edge)
3. **Out of scope** — 2-3 bullet thứ KHÔNG do
4. **MISSING_INFO** — if still thiếu, ghi per format: `MISSING_INFO: [gì] — MUST_ASK: [BA|TechLead|User]`

## KHÔNG do
- No viết user persona, no analyze stakeholder
- No risk matrix, no RICE score
- No sprint plan — việc này scope simple no need
