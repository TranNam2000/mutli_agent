---
SCOPE: simple, bug_fix
TRIGGERS: widget, 1 màn, simple, quick, fix, hotfix
MAX_TOKENS: 3000
---

# QA Skill — Smoke Check (1 màn / fix nhanh)

Test plan nhỏ gọn for 1 màn or 1 bug fix. KHÔNG viết full regression.

## Output

### Test Plan
Table 3-5 cases:
| TC-ID | Title | Given | When | Then | Priority |

### Tối thiểu
- 2 happy path
- 1 edge (boundary / empty / max)
- 1 error (network fail or invalid input)

### Patrol Test Code (widget keys already have)
```dart
// integration_test/smoke_test.dart
import 'package:patrol/patrol.dart';

void main() {
  patrolTest('smoke — happy path', ($) async {
    await $.pumpWidgetAndSettle(MyApp());
    await $(Key('primaryCta')).tap();
    await $(Key('successMessage')).waitUntilVisible();
  });
}
```

### Maestro flow (black-box, không cần code)
```yaml
# maestro/smoke.yaml
appId: com.example.app
---
- launchApp
- tapOn:
    id: "primaryCta"
- assertVisible: "Success"
- takeScreenshot: smoke_happy
```

## Exit criteria
- 100% TC pass trên emulator + 1 real device
- No crash

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Edit` / `Write` to put test code directly into the project (`test/`, `integration_test/`, `__tests__/` — mirror the existing layout). Run the suite via `Bash` once and include pass/fail counts. Reply = summary only.
