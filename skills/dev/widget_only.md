---
SCOPE: simple, bug_fix
TRIGGERS: widget, 1 màn, single, dialog, popup, simple, quick, component
MAX_TOKENS: 4000
---

# Dev Skill — Widget / Simple Screen

Viết 1 StatefulWidget or 1 screen đơn lẻ. KHÔNG use Clean Architecture.

## Output bắt buộc

Mỗi file bắt đầu bằng comment path:
```dart
// lib/features/<feature>/<screen>.dart
```

### Checklist code
- [ ] StatefulWidget + `initState` / `dispose` đúng chỗ
- [ ] Loading / error / empty / success state handle in UI
- [ ] No hardcode string (use const or i18n key)
- [ ] Key for widget to Patrol test nhắm tới:
  `Key('primaryCta')`, `Key('errorMessage')`, `Key('loadingSpinner')`
- [ ] Controller/subscription is dispose
- [ ] No use setState after unmount (mounted check)

## KHÔNG
- No tạo entity / repository / usecase
- No BLoC when chỉ 1-2 state
- No export thêm file bloat

## Luôn cung cấp widget keys for test
Dev PHẢI tự thêm `Key('<id>')` for các element important:
- CTA buttons → `primaryCta`, `secondaryCta`
- Input fields → `<fieldName>Input`
- Error/empty/loading containers
- List items → `item_<index>`

QA will use key này viết Patrol/Maestro test.
