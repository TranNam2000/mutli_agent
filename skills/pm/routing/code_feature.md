---
SCOPE: feature, module
TRIGGERS: tạo, build, làm, viết code, implement, develop, thêm tính năng, them tinh nang, new feature, add feature, code, function, module
STEPS: ba, design, techlead, test_plan, dev, test
AGENTS: BA → Designer → TechLead → QA(plan) → Dev → QA(review)
RESPONSE_FORMAT: code_summary
MAX_TOKENS: 1500
---

# PM Skill — Code Feature (default ship-to-prod)

User muốn tính năng mới có code thực sự deploy được.

## Routing
- Full pipeline 6 step
- BA viết requirement + task list
- Designer mockup UI (skip nếu task không có UI)
- TechLead architecture + sprint plan
- Dev implement → QA review

## Trả lời user (sau khi pipeline xong)
Format gồm:
1. **Tóm tắt** — 2-3 dòng nói tính năng làm gì
2. **File changed** — git diff --stat (số file + số dòng thêm/bớt)
3. **Tests** — pass rate + danh sách test mới
4. **Docs** — đường dẫn `docs/requirements/...`, `docs/arch/...`
5. **Next steps** — nếu QA fail → hướng fix; nếu pass → cách deploy
