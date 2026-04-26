---
SCOPE: simple, feature
TRIGGERS: đổi màu, doi mau, đổi font, doi font, restyle, change color, tweak padding, móc lại, can lai, spacing, redesign button, doi nut, doi chu, update copy, font size, alignment
STEPS: design, dev, test
AGENTS: Designer → Dev → QA
RESPONSE_FORMAT: visual_diff
MAX_TOKENS: 1200
---

# PM Skill — UI Tweak (cosmetic only)

User muốn đổi visual: màu/font/spacing/copy. **Không** logic mới.

## Routing
- **STEPS: design, dev, test**  — bỏ BA/TechLead (không có business rule mới)
- Designer: spec màu/font/spacing chính xác (token, không hardcode hex)
- Dev: apply token vào widget/component
- QA: visual regression (screenshot diff nếu có Percy/Chromatic, else manual checklist)

## Trả lời user
1. **Trước/sau** — tóm tắt thay đổi (màu A → B, padding 16 → 24, ...)
2. **Files** — file styling/theme đã edit
3. **Screenshot** — nếu có (Maestro flow chạy được)
4. **Token reuse** — nói rõ token nào đã dùng/thêm để không drift sau này
