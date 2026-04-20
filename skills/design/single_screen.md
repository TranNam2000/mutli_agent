---
SCOPE: simple, bug_fix
TRIGGERS: 1 màn, screen, widget, dialog, popup, single
MAX_TOKENS: 2500
---

# Design Skill — Single Screen

You thiết kế 1 màn hình / 1 component. KHÔNG viết design system toàn app.

## Output bắt buộc

### 1. Screen Layout (ASCII wireframe)
```
┌────────────────────────────┐
│  [back]   Title     [icon] │
│                            │
│  Primary content…          │
│                            │
│  [Primary CTA]             │
└────────────────────────────┘
```

### 2. Component Specs
- Button: width/height, color (hex), radius, text style
- Input: placeholder, error state, helper text
- Icon: size pt, color

### 3. States (bắt buộc enough 4)
- Loading: spinner + skeleton placeholder
- Empty: illustration + CTA
- Error: message + retry
- Success: content final

### 4. Interactions
- Tap, long press, swipe (if có)
- Animation: duration + easing

### 5. Tokens used
- Colors (reuse from existing design system if maintain mode)
- Typography levels
- Spacing (base-4 grid)

## KHÔNG
- No redesign toàn app
- No đề xuất design system new
