---
SCOPE: feature, module
TRIGGERS: nestjs, nest, node backend, api server, rest api, graphql, typeorm, prisma
MAX_TOKENS: 4000
---

# TechLead Skill — NestJS Backend Architecture

## Output bắt buộc

### 1. Folder structure
```
src/
  modules/<feature>/
    <feature>.module.ts
    <feature>.controller.ts
    <feature>.service.ts
    dto/
    entities/
    tests/
  common/
    guards/
    interceptors/
    filters/
    pipes/
  config/
  main.ts
```

### 2. State + data layer
- ORM: TypeORM | Prisma — pick one + reason
- Migration: TypeORM CLI / Prisma migrate
- Redis cache layer if read-heavy
- Queue: Bull / BullMQ for background jobs

### 3. Validation + error handling
- DTO + class-validator
- Global exception filter trả về error format chuẩn
- Logging: Pino / Winston cho structured log

### 4. Auth strategy
- JWT + Passport
- Guard route theo role
- Refresh token rotation

### 5. Testing approach
- Unit test cho service (Jest)
- Integration test cho controller (supertest)
- E2E test với test DB

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Glob` / `Grep` / `Read` to survey the actual codebase first. Use `Edit` / `Write` to save architecture docs into `docs/arch/<feature>.md`. Echo task assignment + sprint plan in the reply.
