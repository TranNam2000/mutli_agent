---
SCOPE: full_app
TRIGGERS: full app, mvp, regression, release, platform, entire
MAX_TOKENS: 10000
---

# QA Skill — Full App Regression Suite

Toàn bộ test strategy for MVP release. Kết hợp 5 tầng test.

## Output

### 1. Test Strategy Document

**Tầng test (pyramid inverted for mobile):**
1. Unit (domain layer) — 70% coverage target, cypress fast
2. Widget (flutter_test) — key widgets
3. Integration (Patrol) — feature flows end-to-end có code
4. E2E (Maestro) — black-box user journeys
5. Manual exploratory — session-based, 1 session/feature

### 2. Test Plan per Module
Mỗi module P0 có table TC đầy enough:
- Positive, negative, boundary, error scenarios
- Cross-module integration test cases
- Performance/load test for critical path

### 3. Patrol Suite
- `integration_test/` mirror `lib/features/`
- Bootstrap app with test DI override
- Reset state giữa mỗi test

### 4. Maestro E2E Suite
```
maestro/
  smoke/          # run mỗi PR
  regression/     # run before release
  critical_flows/ # happy path of mọi module
  visual_diff/    # screenshot baseline
```

Fromng flow có tags: smoke / regression / release / visual.

### 5. Visual Regression
- Baseline screenshots stored in `maestro/baselines/`
- Tolerance 3% pixel diff default, 0% for brand assets
- Auto-update baseline qua PR review

### 6. Performance Test
Sử dụng Flutter DevTools profiling:
- Frame time P95 < 16ms
- Memory < budget
- Startup time < 2s cold
- APK size < 40MB

### 7. Accessibility Test
- Semantic labels coverage 100% for interactive
- Contrast ratio check via axe-like scan
- TalkBack / VoiceOver manual pass for critical flow

### 8. Security / Privacy Test
- HTTPS enforced (cert pinning test)
- Token no leak into logs
- Biometric gating verified
- Data deletion GDPR compliant

### 9. Localization Test
Key flow try with tiếng Việt + tiếng Anh + layout RTL check.

### 10. Release Gating Matrix

| Tier | Criteria |
|------|----------|
| MVP | P0 pass 100%, P1 ≥ 95%, no BLOCKER |
| Beta | Full regression pass, < 5 MINOR bugs |
| Store | Performance targets met, a11y audit pass |

### 11. CI hook
```yaml
# .github/workflows/test.yml
on: [push, pull_request]
jobs:
  unit:
    runs: flutter test
  widget:
    runs: flutter test --tags widget
  patrol:
    runs: patrol test -t integration_test/
  maestro-smoke:
    runs: maestro test maestro/smoke/
```

### 12. Bug Triage Process
- SLA: BLOCKER 2h, CRITICAL 1 day, MAJOR 3 days
- Escalation: QA → TechLead → PM when blocker no resolve in SLA

### 13. Metrics
- Test pass rate trend
- Flaky test rate (target <2%)
- Bug escape rate (prod bugs / pre-release bugs)
