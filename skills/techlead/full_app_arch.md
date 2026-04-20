---
SCOPE: full_app
TRIGGERS: full app, mvp, platform, super app, multi-module, entire app, new app
MAX_TOKENS: 10000
---

# TechLead Skill — Full App Architecture

Toàn bộ app new / MVP / multi-module. Output enough to team 5-10 người triển khai 3-6 tháng.

## Output bắt buộc

### 1. Tech Stack + Rationale
- Flutter version, Dart version
- State management (BLoC / Riverpod / GetX) — forose 1, giải thích tại why
- Network layer (dio + interceptors)
- Local storage (Hive / Drift / Isar) — forose 1
- DI (get_it / injectable)
- Routing (go_router / auto_route)
- CI/CD (Codemagic / GitHub Actions / Bitrise)

### 2. Modular Monolith Structure
```
lib/
  core/
    network/, storage/, di/, theme/, routing/, logging/, error/
  features/
    <module_1>/{data,domain,presentation}
    <module_2>/…
  shared/
    widgets/, utils/, constants/
  app/
    app.dart, app_router.dart, app_bloc_observer.dart
  main.dart, main_dev.dart, main_prod.dart (flavors)
```

### 3. Module Boundaries
- Mỗi module có public API qua `<module>.dart` barrel
- KHÔNG import thẳng giữa feature/A → feature/B — đi qua shared event bus / route

### 4. Data Layer Architecture
- Repository interface (domain) vs impl (data)
- Offline-first strategy if có
- Cache invalidation rules
- Pagination contract

### 5. API Layer (if có backend)
Bảng endpoint:
| Method | Path | Purpose | Auth | Rate limit |
|--------|------|---------|------|------------|
| POST | /auth/login | Login | no | 5/min |
| GET | /users/me | Profile | yes | 60/min |
…

### 6. Error Model
```
AppError {
  code: ErrorCode
  userMessage: String (i18n key)
  technicalMessage: String
  retryable: bool
}
```

### 7. Security & Compliance
- Auth flow (OAuth2 / JWT refresh)
- Token storage (FlutterSecureStorage)
- Biometric gating for sensitive actions
- Cert pinning if backend nội bộ
- GDPR / data retention if có user

### 8. Observability
- Analytics events (bảng event + properties)
- Crashlytics / Sentry
- Performance monitoring (Firebase Performance / custom tracing)
- Logging levels

### 9. CI/CD Pipeline
```
PR → lint + unit test + widget test
Merge main → integration test + build staging APK/IPA
Tag release → build prod + upload to stores
```

### 10. Testing Strategy
- Unit: 80% coverage for domain layer
- Widget: key components + screens
- Integration (Patrol): happy path / critical flows
- E2E (Maestro): user journeys

### 11. Performance Budget
- App startup < 2s (cold)
- Screen transition < 300ms
- Memory < 250MB idle
- APK size < 40MB release

### 12. Milestones (map with BA roadmap)
- Sprint 1-2: Core + Auth module
- Sprint 3-4: Main feature module
- Sprint 5-6: Polish + non-core modules
- Sprint 7-8: Store submission + A/B testing

### 13. Risks & Mitigations
Top 5 tech risks, severity, mitigation.

## Quy tắc
- KHÔNG copy-paste template — phải justify mỗi decision
- If team nhỏ (<3 người) → đề xuất cắt scope, no over-architect
