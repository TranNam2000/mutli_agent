---
SCOPE: simple, feature, module
TRIGGERS: viết tài liệu, xây dựng tài liệu, tạo tài liệu, build doc, build docs, write doc, write docs, write documentation, document, doc, spec, đặc tả, tài liệu kỹ thuật, requirement doc, prd, brd, technical spec, architecture doc
STEPS: ba
MAX_TOKENS: 1500
---

# PM Skill — Documentation / Spec Build

Task chỉ viết tài liệu, **không** code. Output: file markdown trong `docs/`.

## Đặc điểm
- Không tạo/sửa source code
- Không cần Dev/Test step
- BA chịu trách nhiệm chính (viết content)
- TechLead optional — chỉ thêm nếu doc về architecture

## Routing
- **KIND**: `feature` (variant lean)
- **STEPS**: `ba` only (mặc định)
  - Thêm `techlead` nếu là `docs/arch/<feature>.md` (architecture-level)
  - Thêm `design` nếu là `docs/design/<screen>.md` (UI mockup spec)
- **Confidence**: 0.9 nếu user nói rõ "viết/xây dựng tài liệu"

## Sub-pipeline
```
DYNAMIC_STEPS: ba                              # default
DYNAMIC_STEPS: ba, techlead                    # nếu doc architecture
DYNAMIC_STEPS: ba, design                      # nếu doc UI/UX
```

## KHÔNG
- Đừng include `dev` — không có code để viết
- Đừng include `test` — không có code thì test cái gì
- Đừng include `test_plan` — không deploy gì cả
