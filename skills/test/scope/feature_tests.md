---
SCOPE: feature, module
TRIGGERS: feature, flow, api, integration, regression
MAX_TOKENS: 6000
---

# QA Skill — Feature Tests (Patrol + Maestro)

Test plan for 1 feature có API + multi-screen flow. Kết hợp Patrol (in app) + Maestro (black-box E2E).

## Output

### 1. Test Strategy
- Scope: feature nào, module nào
- Risk areas (list 3-5)
- Tooling: Patrol for widget+integration có code, Maestro for user journey black-box

### 2. Test Cases
Table đầy enough: TC-ID | Title | Given | When | Then | Priority | Type (unit/widget/integration/e2e)

**Tối thiểu mỗi feature:**
- 3 happy path
- 3 edge (empty list / max pagination / slow network)
- 3 negative (invalid input / 401 / 500 error)
- 2 offline (airplane mode / no network during submit)

### 3. Patrol Integration Test
```dart
// integration_test/<feature>_test.dart
import 'package:patrol/patrol.dart';

void main() {
  patrolTest('TC-001: login with valid credentials', ($) async {
    await $.pumpWidgetAndSettle(app);
    await $(LoginKeys.email).enterText('user@test.com');
    await $(LoginKeys.password).enterText('Passw0rd!');
    await $(LoginKeys.submit).tap();
    await $(HomeKeys.root).waitUntilVisible();
  });

  patrolTest('TC-002: network error shows retry', ($) async {
    // mock network fail
    …
    await $(Key('errorRetry')).waitUntilVisible();
    await $(Key('errorRetry')).tap();
  });
}
```

### 4. Maestro E2E Flows
Mỗi critical journey 1 YAML:
```yaml
# maestro/<feature>/happy_path.yaml
appId: com.example.app
tags: [smoke, p0]
---
- launchApp:
    clearState: true
- tapOn: "Login"
- inputText: "user@test.com"
- tapOn: { id: "login_password" }
- inputText: "Passw0rd!"
- tapOn: "Submit"
- assertVisible:
    text: "Welcome"
    timeout: 10000
- takeScreenshot: ${FEATURE}_happy
```

### 5. Performance Targets
- Screen load < 1.5s (P95)
- API response render < 300ms after data
- Scroll jank < 16ms/frame

### 6. Exit Criteria
- P0+P1 pass rate ≥ 95%
- No BLOCKER / CRITICAL bug
- Maestro screenshot diff vs design < 5% pixel difference

### 7. Test Data
- Test accounts ghi rõ credentials
- Mock data fixtures paths
- Reset state strategy

## Quy tắc
- không viết test giả định key not yet có — report FAIL "missing key" to Dev thêm
- Mỗi TC map with 1 FR in PRD

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Edit` / `Write` to put test code directly into the project (`test/`, `integration_test/`, `__tests__/` — mirror the existing layout). Run the suite via `Bash` once and include pass/fail counts. Reply = summary only.
