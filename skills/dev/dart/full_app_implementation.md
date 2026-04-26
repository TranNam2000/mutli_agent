---
SCOPE: full_app
TRIGGERS: full app, mvp, entire app, platform, multi-module, new app
MAX_TOKENS: 12000
---

# Dev Skill — Full App Implementation

Build toàn bộ app per architecture TechLead  vạch. Output can dài — phải cover enough.

## Strategy
Because không viết hết is 1 time, chia output thành **batch**:
- **Batch 1: Core** — DI, routing, theme, network, storage, error, logging
- **Batch 2: Auth module** — login/register/forgot password
- **Batch 3: Main feature P0 module** — 1 module important nhất
- **Batch 4: Shared widgets + utils**
- Các batch tiếp per chạy qua resume / update mode

Nói rõ `BATCH X/N — ...` trong output to orchestrator biết bao hour quay lại.

## Quy tắc mỗi batch
- Comment header for MỖI file: `// lib/<path>.dart`
- Code production-ready: no TODO, no `throw UnimplementedError`
- Mỗi public API có dartdoc `///`
- Lint level: `very_good_analysis` tuân thủ

## Widget keys
Mỗi screen chính có key map đầu file:
```dart
abstract class LoginKeys {
  static const email = Key('login_email');
  static const password = Key('login_password');
  static const submit = Key('login_submit');
  static const errorBanner = Key('login_error');
}
```

## Router (go_router)
- Declarative routes ở `app_router.dart`
- Redirect logic for auth gate
- Deep link handling

## DI registration
Mỗi module có `<module>_injection.dart` with:
```dart
void register<Module>(GetIt sl) { ... }
```
Gọi from `injection_container.dart` tổng.

## Testing hook
- `analysis_options.yaml` strict
- `test/` mirror `lib/` path
- Mỗi BLoC/Cubit phải có test
- Golden tests for key screens

## Flavors & env
- `main_dev.dart` / `main_prod.dart`
- `.env` files (use `flutter_dotenv`)
- No commit API key

## Output format
Mỗi file 1 code block, no nhét 10 file into 1 block:
```dart
// lib/core/di/injection_container.dart
…
```

## If thiếu thông tin
Ghi rõ cuối output:
```
MISSING_INFO: Icon pack forose material or cupertino — MUST_ASK: Design
MISSING_INFO: Backend endpoint staging URL — MUST_ASK: TechLead
```

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Read` to learn existing structure, then `Edit` / `Write` directly. Prefer editing existing files over creating parallel copies. Output only a summary of what you changed — no source blocks.
