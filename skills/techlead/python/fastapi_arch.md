---
SCOPE: feature, module, full_app
TRIGGERS: python backend arch, fastapi arch, django arch, python service arch
MAX_TOKENS: 4000
---

# TechLead Skill — Python Backend Architecture

## Output bắt buộc

### 1. Layered structure
```
app/
  api/          # HTTP layer (FastAPI routers)
  schemas/      # Pydantic request/response
  services/     # business logic
  models/       # SQLAlchemy ORM
  db/           # session, migrations alembic
  core/         # config, security, deps
  tests/
```

### 2. Stack baseline
- Framework: FastAPI (async) | Django REST (sync, batteries) — pick + reason
- ORM: SQLAlchemy 2.x async + Alembic migrations
- Validation: Pydantic v2
- Job queue: Celery / RQ / arq (async)
- Cache: Redis (cache-aside pattern)

### 3. Auth + security
- JWT (python-jose) hoặc session cookie
- OAuth2 + OpenID Connect cho B2B
- Rate limit: slowapi
- CORS chuẩn theo env

### 4. DB design notes
- Connection pool size theo workers
- Read replica routing nếu read-heavy
- Migration strategy zero-downtime (add column nullable → backfill → enforce NOT NULL)

### 5. Observability
- Structured log (loguru / structlog) JSON output
- OpenTelemetry traces (FastAPI auto-instrument)
- Sentry cho error
- Metrics: prometheus-fastapi-instrumentator

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Glob` / `Grep` / `Read` to survey the actual codebase first. Use `Edit` / `Write` to save architecture docs into `docs/arch/<feature>.md`. Echo task assignment + sprint plan in the reply.
