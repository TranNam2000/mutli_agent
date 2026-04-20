---
SCOPE: feature, module, full_app
TRIGGERS: nestjs, nest.js, nest, backend api, rest api, microservice, graphql backend, typeorm, prisma backend, express, bullmq, websockets
MAX_TOKENS: 8000
---

# TechLead Skill — NestJS backend architecture

Use for backend requests built on NestJS. Output the full Technical
Architecture Document per pipeline format, applying the patterns below.

## Default module layout

```
src/
├── main.ts                    # bootstrap + global pipes/filters
├── app.module.ts              # root module
├── config/                    # ConfigModule with Joi validation
├── common/
│   ├── filters/               # global exception filter
│   ├── guards/                # JwtAuthGuard, RolesGuard
│   ├── interceptors/          # LoggingInterceptor, TransformInterceptor
│   ├── pipes/                 # ValidationPipe
│   └── decorators/            # @CurrentUser(), @Roles()
├── modules/
│   └── <domain>/
│       ├── <domain>.controller.ts
│       ├── <domain>.service.ts
│       ├── <domain>.module.ts
│       ├── dto/
│       ├── entities/
│       ├── repositories/
│       └── tests/
├── database/                  # TypeORM/Prisma config + migrations
├── queues/                    # BullMQ processors
└── websockets/                # gateways if present
```

## Architectural decisions to enforce

1. **DI scopes** — use `DEFAULT` scope unless request needs `REQUEST` scope
   (e.g. multi-tenant). Document decision in the spec.
2. **Validation** — class-validator on every DTO, global `ValidationPipe`
   with `whitelist: true, transform: true`.
3. **Error model** — custom `DomainException` classes mapped by
   `HttpExceptionFilter` to `{statusCode, message, code, details}`.
4. **Transactions** — use TypeORM `@Transactional()` decorator or Prisma
   `$transaction`; never ad-hoc service composition across aggregates.
5. **Auth** — JWT via Passport strategy + `JwtAuthGuard`; refresh token
   rotation if request mentions "stay logged in".
6. **Rate limit** — `@nestjs/throttler` on public endpoints by default.
7. **Logging** — Pino via `nestjs-pino`, structured JSON, request id
   correlation via `CLS` middleware.
8. **Queues** — BullMQ with Redis; one queue per bounded context. Dead-
   letter handling mandatory.

## API style defaults

- REST: kebab-case paths, plural nouns, versioned via URI (`/api/v1/...`).
- GraphQL (if mentioned): code-first with `@nestjs/graphql`, one resolver
  per module.
- Response envelope: `{ data, meta, error }` consistent across REST.

## Deliverables in TechLead output

Always include:
- Module graph (Mermaid or ASCII)
- DB schema or entity list
- Endpoint list with method + path + auth + validation DTO
- Sequence diagram for the most complex happy path
- Deployment notes (Docker/K8s if relevant, env vars required)

## Non-goals (out of scope — flag as MISSING_INFO if asked)

- Frontend integration details
- Infrastructure provisioning (Terraform/Pulumi)
- Load-test budgets unless explicitly in the request
