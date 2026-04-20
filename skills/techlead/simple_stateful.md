---
SCOPE: simple, bug_fix
TRIGGERS: widget, 1 màn, single screen, simple, quick, component
MAX_TOKENS: 2500
---

# TechLead Skill — Simple / StatefulWidget

Task is 1 màn nhỏ or 1 widget — KHÔNG Clean Architecture.

## Output bắt buộc (≤ 1 trang)

### 1. File list
- `lib/features/<feature>/<screen>.dart` — main screen
- If có state: `lib/features/<feature>/<screen>_state.dart`

### 2. State Management
- StatefulWidget if state < 5 fields
- ValueNotifier if need 1-2 widget other listen

### 3. Dependencies need thêm into pubspec.yaml (if có)
### 4. Task Breakdown
- Task 1: UI skeleton — 1h
- Task 2: State wiring — 30m
- Task 3: Error/loading states — 30m

### 5. No over-engineer
- KHÔNG tạo entity/repository/usecase
- KHÔNG use BLoC for state đơn giản
