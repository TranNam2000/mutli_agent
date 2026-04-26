---
SCOPE: feature, module
TRIGGERS: kotlin, android native, jetpack compose, compose, viewmodel, hilt, room, retrofit, kmp, kotlin multiplatform
MAX_TOKENS: 4000
---

# Dev Skill — Android Native (Kotlin + Compose)

## Output bắt buộc
- UI: Jetpack Compose (Material 3)
- DI: Hilt
- Async: Kotlin Coroutines + Flow
- Network: Retrofit + Moshi/Kotlinx-serialization
- Local: Room + DataStore

### File layout (single module)
```
app/src/main/java/com/<org>/<app>/
  ui/<feature>/{Screen.kt, ViewModel.kt, State.kt, Event.kt}
  data/{api, db, repository}/
  domain/{model, usecase}/
  di/<Module>.kt
  navigation/<Graph>.kt
  MainActivity.kt
```

### Checklist
- [ ] ViewModel expose `StateFlow<UiState>` (sealed/data class), không LiveData
- [ ] Screen composable nhận `uiState`, `onEvent` lambda — preview-friendly
- [ ] Coroutine scope: `viewModelScope` cho VM, `lifecycleScope` cho UI
- [ ] Loading / error / empty / success state mọi data screen
- [ ] Compose preview cho mỗi screen + state variation
- [ ] Theme + color qua `MaterialTheme`, không hardcode color
- [ ] resource: string ra `strings.xml` (i18n ready)

### KHÔNG
- Đừng mix Compose + XML View trừ khi cần migrate dần
- Đừng dùng GlobalScope
- Đừng `runBlocking` trên main thread
- Đừng truyền Context vào ViewModel (dùng `Application` qua Hilt nếu cần)

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Read` + `Edit` / `Write` directly on real project files. Run `Bash` static-analysis (e.g. `mypy`, `ruff`, `flutter analyze`, `gradle :app:lint`) before finishing. Output only a summary of what you changed.
