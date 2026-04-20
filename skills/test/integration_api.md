---
SCOPE: feature, module, bug_fix
TRIGGERS: integration test, api test, contract test, supertest, e2e api, pact, endpoint test, rest test, graphql test, backend test, /api/, api endpoint, http test, test plan for /, integration spec
MAX_TOKENS: 6500
---

# Test Skill — API integration testing

Use when the task under test is a backend endpoint, microservice, or
inter-service contract. Produces an integration test plan distinct from
unit tests.

## Test layering we assume

```
unit         (fast, in-memory, per class)        — owned by Dev
integration  (process-level, real DB/Redis)      ← THIS SKILL
e2e          (full stack, real auth + browser)   — owned by QA smoke
contract     (consumer-driven, Pact)             — optional, if cross-service
```

## Framework matrix

| Stack          | Runner       | HTTP helper           | DB bootstrap     |
|----------------|--------------|-----------------------|------------------|
| NestJS         | Jest         | `supertest`           | `docker-compose` or testcontainers |
| Express        | Jest/Vitest  | `supertest`           | same             |
| FastAPI        | pytest       | `TestClient`          | `pytest-postgres`|
| Django         | pytest-django| `APIClient`           | `pytest.fixtures`|
| Go             | `go test`    | `net/http/httptest`   | `dockertest`     |

Pick based on the stack hint in task/metadata. If ambiguous, default to
the stack that TechLead's architecture doc specified.

## Test file layout (NestJS example)

```
test/
├── integration/
│   ├── users.e2e-spec.ts
│   ├── products.e2e-spec.ts
│   └── helpers/
│       ├── test-app.ts        # module factory
│       ├── db-cleanup.ts      # truncate tables between tests
│       └── auth.ts            # login helper → returns JWT
└── jest-e2e.json
```

## Test coverage rubric

Per endpoint (or per operation for GraphQL), cover at least:

1. **Happy path** — 2xx with canonical payload.
2. **Validation failure** — at least 2 subcases (missing field, wrong
   type).
3. **Authorization** — 401 without token, 403 with wrong role.
4. **Business rule boundary** — one case per documented AC.
5. **Persistence side effect** — verify DB state after call.
6. **Idempotency** — same request twice produces same end state (for
   POST use idempotency key if present).
7. **Transaction rollback** — simulate downstream failure, assert no
   partial writes.

## Performance smoke (optional, one per module)

Add a single test that fires N=50 concurrent requests to the hot path
and asserts p95 < threshold. Gate on env var so it can be skipped in
CI.

## Test data strategy

- **Per-test transactional rollback** when DB supports savepoints.
- **Truncate-and-seed** fallback when transaction isolation isn't
  feasible (e.g. queue consumers).
- **Never share state between test files** — each `*.e2e-spec.ts`
  instantiates its own `TestingModule` via `beforeAll`.

## Assertions must check

- HTTP status code
- Response body shape (schema + sample values)
- Side-effect: DB row exists / updated / deleted
- Side-effect: queue/event published (spy on publisher)
- No unintended writes (count-before-after on unrelated tables)

## Output format

Ordered spec files per feature, each with `describe` groups matching
the rubric above. Include the docker-compose snippet if the test needs
infra beyond what's already running.

## Exit criteria to report

- Coverage ≥ 80% on the module under test
- p95 latency (if perf smoke included)
- List of AC ids not yet covered (cross-ref BA output)
