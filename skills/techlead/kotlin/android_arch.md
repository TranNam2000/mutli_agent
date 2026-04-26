---
SCOPE: feature, module, full_app
TRIGGERS: android architecture, kotlin arch, compose arch, multi-module android, gradle module split
MAX_TOKENS: 4000
---

# TechLead Skill — Android Architecture (Kotlin)

## Output bắt buộc

### 1. Module split (multi-module)
```
:app                              # entry, navigation graph
:core:designsystem                # theme, components, tokens
:core:network, :core:database     # retrofit, room
:core:common                      # utils, dispatchers
:feature:<name>                   # screens, viewmodels per feature
```
Dependency rule: `:feature:*` không phụ thuộc lẫn nhau, đi qua `:core:*`.

### 2. Layered (per module)
```
ui/        – Compose screens + ViewModels
domain/    – use cases + models
data/      – repository + API/DB sources
di/        – Hilt modules
```

### 3. Stack baseline
- Compose + Material 3
- DI: Hilt
- Persistence: Room (SQLite); DataStore cho preferences
- Network: Retrofit 2 + OkHttp + Moshi/Kotlinx-serialization
- Async: Kotlin Coroutines + Flow
- Navigation: Compose Navigation (or Voyager nếu nested deep)
- Image: Coil 3
- Test: JUnit5 + MockK + Turbine + Compose UI test

### 4. Build & flavor
- Gradle Version Catalog (`libs.versions.toml`)
- Convention plugins cho compile/Compose/test setup
- Flavor: dev/staging/prod (BuildConfig URL khác nhau)
- ProGuard/R8 rules cho release

### 5. Observability
- Crashlytics
- Sentry / Bugsnag tuỳ tổ chức
- Performance Monitoring (Firebase) cho cold start, screen render

### 6. CI
- GitHub Actions / Bitrise: lint + test + assembleRelease + Play upload
- Robolectric cho Compose-screen test offline

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Glob` / `Grep` / `Read` to survey the actual codebase first. Use `Edit` / `Write` to save architecture docs into `docs/arch/<feature>.md`. Echo task assignment + sprint plan in the reply.
