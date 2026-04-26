---
SCOPE: feature, module
TRIGGERS: python, fastapi, flask, django, api server python, py backend, asyncio, pydantic
MAX_TOKENS: 4000
---

# Dev Skill — Python FastAPI Backend

## Output bắt buộc
- App = FastAPI + Pydantic v2 models + SQLAlchemy 2.x async
- Type hints mọi function (mypy strict)

### File layout
```
app/
  main.py
  api/v1/<resource>.py        # router + endpoints
  models/                      # SQLAlchemy ORM
  schemas/                     # Pydantic request/response
  services/                    # business logic (no FastAPI imports)
  db/{base, session}.py
  core/{config, security, deps}.py
  tests/
```

### Checklist
- [ ] Dependency injection qua `Depends()`, không global state
- [ ] Async DB session per request, đóng đúng cách
- [ ] Pydantic schema tách Request/Response (không leak ORM fields)
- [ ] Exception → HTTPException with detail dict, không trả raw error
- [ ] Pagination chuẩn (limit/offset hoặc cursor)
- [ ] OpenAPI tag + summary mỗi endpoint
- [ ] pytest + httpx.AsyncClient cho test, không đụng prod DB

### KHÔNG
- Đừng dùng sync SQLAlchemy nếu app chạy async
- Đừng để business logic trong router — đẩy sang `services/`
- Đừng commit secret vào `core/config.py` — dùng env + Pydantic Settings

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Read` + `Edit` / `Write` directly on real project files. Run `Bash` static-analysis (e.g. `mypy`, `ruff`, `flutter analyze`, `gradle :app:lint`) before finishing. Output only a summary of what you changed.
