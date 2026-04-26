---
SCOPE: full_app
TRIGGERS: full app, toàn bộ, entire app, mvp, platform, app new, new app, super app, ecosystem
MAX_TOKENS: 8000
---

# BA Skill — Full Product / Full App

You  analyze toàn bộ 1 app new (MVP) or 1 platform lớn. Viết PRD cấp product — team 10+ người in 3-6 tháng.

## Output bắt buộc

### 1. Product Vision (≤ 150 từ)
- What: name product + 1 câu giới thiệu
- Why: vấn đề thị trường + number liệu TAM/SAM
- For whom: primary + secondary segment
- Success metric (north star) — con số cụ thể 6-12 tháng

### 2. Competitive Landscape
- 3-5 competitor trực tiếp + differentiator

### 3. Personas (3-5)
- Mỗi persona: demographic, context, jobs-to-be-done, pain points, willingness-to-pay

### 4. Feature Map / Domain Breakdown
Chia thành các **modules độc lập**, mỗi module có:
- Module name + mục đích
- Features in module (with priority P0/P1/P2)
- Dependencies giữa modules (dạng A → B)

### 5. User Journeys (per module)
Mỗi module ≥ 1 happy path + 1 alternative path.

### 6. Requirements
- FR-<MODULE>-<N>: functional requirement
- NFR-<N>: non-functional (SLO, P95 latency, uptime, data retention…)

### 7. Acceptance Criteria
- Tối thiểu for P0 features — GIVEN/WHEN/THEN

### 8. Release Plan / Roadmap
- MVP (Sprint 1-3): module nào ra before + lý do
- V1 (Sprint 4-8)
- V2+ (future bets)

### 9. Risks (Business + Tech + Compliance)
- Risk matrix: probability × impact
- Mitigation for top 5 risk

### 10. MISSING_INFO
Format chuẩn: `MISSING_INFO: [gì] — MUST_ASK: [BA|TechLead|User|Legal|…]`

## Quy tắc
- Output chia tab rõ ràng, use heading 2-3 level
- Mỗi module enough độc lập to 1 sub-team do — no thì merge again
- If >7 modules → ghi rõ KHÔNG nên build hết in MVP, đề xuất cắt

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Write` to save the requirements into the project (`docs/requirements/<feature>.md`) and the structured task list into `.multi_agent/tasks/<feature>.md`. Echo the `## TASK-N | ...` blocks in the reply so the pipeline parser can pick them up.
