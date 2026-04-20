---
SCOPE: feature, module
TRIGGERS: flow, feature, onboarding, checkout, search, profile, settings
MAX_TOKENS: 5000
---

# Design Skill — Feature / Multi-screen Flow

You thiết kế 1 feature có nhiều màn or 1 module. Đảm bảo nhất quán cross-screen.

## Output bắt buộc

### 1. Feature Flow Diagram
```
[Entry] → [Screen A] → [Screen B] → [Success]
              ↓
          [Error State]
```

### 2. Design System (feature-scoped)
If not yet có global design system: tạo tối thiểu
- Colors: primary, secondary, semantic (success/warning/error/info) — có hex
- Typography: display / heading / body / caption
- Spacing: 4/8/12/16/24/32
- Radius: 4/8/12/16
- Shadows: 3 levels (card/modal/popup)

If có global design system → tham chiếu again, KHÔNG copy toàn bộ.

### 3. Screens
Mỗi screen (3-5 màn):
- ASCII wireframe
- Component list
- 4 states (loading/empty/error/success)

### 4. Navigation Map
- `from màn A → màn B` qua action gì
- Modal vs push vs bottom sheet

### 5. Interaction Patterns
- Consistent gestures in feature
- Animation timing + easing

### 6. A11y Checklist
- Contrast ratios for text chính
- Touch target ≥ 44pt
- VoiceOver labels for icon-only buttons

## KHÔNG
- No design màn outside feature scope
- No over-design when brief bảo "MVP simple"
