---
SCOPE: feature, module
TRIGGERS: feature, flow, user story, sprint, tính năng, chức năng, module, onboarding
MAX_TOKENS: 4000
---

# BA Skill — Feature / Module

You  analyze 1 feature có nhiều màn (login flow, checkout, search…) or 1 module hoàn chỉnh. Viết PRD enough to team 3-5 người implement in 1-2 sprint.

## Output bắt buộc (1-3 trang)

### 1. Overview
- Vấn đề user  gặp (1-2 câu, có number liệu if có)
- Mục tiêu đo lường is (north-star metric)

### 2. Personas (tối thiểu 2)
- Name + context + jobs-to-be-done

### 3. User Journeys
- Mỗi journey: entry → actions → exit
- Happy path + 1-2 alternative path

### 4. Functional Requirements
- FR-001 → FR-NNN — mỗi FR có priority P0/P1/P2

### 5. Acceptance Criteria
- Mỗi FR có AC dạng GIVEN/WHEN/THEN, ≥ 2 cases

### 6. Non-functional
- Performance targets (P95 time, bundle size…)
- A11y (contrast, screen reader)
- Offline behavior if có

### 7. Risks & Mitigation
- Tối thiểu 3 risk có severity + mitigation plan

### 8. MISSING_INFO (if có)
Format: `MISSING_INFO: [gì] — MUST_ASK: [BA|TechLead|User]`

## Quy tắc
- No mô tả HOW (to TechLead/Dev lo)
- Mỗi FR phải map is with AC cụ can
- If feature need API/data external → ghi rõ integration point
