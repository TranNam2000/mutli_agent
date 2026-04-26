---
SCOPE: simple, feature
TRIGGERS: prototype, poc, proof of concept, demo, mockup, throwaway, hackathon, mvp lite, sketch, concept
STEPS: dev, test
MAX_TOKENS: 1500
---

# PM Skill — Prototype / Demo

Mục tiêu: **chứng minh ý tưởng**, KHÔNG production. Code throw-away.

## Đặc điểm
- Thời gian: vài giờ → 1-2 ngày
- Output: chạy được trên máy/demo, KHÔNG cần test/CI/security
- Audience: stakeholder demo, hackathon, design review

## Routing
- **KIND**: `feature` (lean variant)
- **STEPS**: `dev, test` chỉ — bỏ qua BA/Design/TL/test_plan
- **Confidence**: 0.9 nếu user nói rõ "prototype/poc/demo"

## Sub-pipeline gợi ý
```
DYNAMIC_STEPS: dev, test
```

## KHÔNG
- Đừng yêu cầu Clean Architecture
- Đừng add unit test (smoke test đủ)
- Đừng yêu cầu performance metric
- Đừng setup CI/CD

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Read` to check existing docs/config. No code edits.
