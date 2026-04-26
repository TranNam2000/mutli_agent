---
SCOPE: feature, module
TRIGGERS: feature, flow, api, state, cubit, bloc, local db, offline
MAX_TOKENS: 5000
---

# TechLead Skill — Feature with BLoC/Cubit

Feature có state phức tạp, API call, or local storage. Use BLoC/Cubit + Repository pattern.

## Output bắt buộc

### 1. Folder Structure (tree thật)
```
lib/features/<feature>/
  data/
    models/<name>_model.dart
    datasources/<name>_remote_ds.dart
    datasources/<name>_local_ds.dart
    repositories/<name>_repository_impl.dart
  domain/
    entities/<name>.dart
    repositories/<name>_repository.dart
    usecases/<verb>_<noun>.dart
  presentation/
    bloc/<name>_cubit.dart
    bloc/<name>_state.dart
    pages/<screen>_page.dart
    widgets/<widget>.dart
```

### 2. API Contracts (nếu có network)
Mỗi endpoint:
```
GET /resource?param=value
Headers: Authorization, X-Request-ID
Response 200: { field: type, ... }
Response 4xx/5xx: { code, message, details? }
```

### 3. Data Models
- Entity (pure Dart class)
- DTO (JSON serializable)
- Mapping helpers

### 4. State Machine
```
states: initial → loading → success | error | empty
events: Load / Refresh / Retry / Select
```

### 5. Error Handling Strategy
- Network error → retry 3x with exponential backoff
- 4xx → show error to user, no retry
- Parse error → log + fallback UI

### 6. Security
- Token lưu in FlutterSecureStorage
- PII no cache
- HTTPS only

### 7. Task Breakdown (estimate hour)
- T1: Entity + Repository interface — 1h
- T2: Remote datasource + tests — 2h
- T3: Repository impl + mapping — 1.5h
- T4: Cubit + states — 1.5h
- T5: UI pages + widget states — 3h
- T6: Integration + E2E — 2h

### 8. Dependencies
- dio / http
- flutter_bloc
- get_it (DI)
- flutter_secure_storage

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Glob` / `Grep` / `Read` to survey the actual code first. Use `Edit` / `Write` to save architecture docs into `docs/arch/<feature>.md`. Echo the task assignment + sprint plan in the reply for downstream parsing.
