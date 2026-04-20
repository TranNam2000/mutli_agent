---
SCOPE: feature, module, bug_fix
TRIGGERS: nestjs, nest.js, nest, backend, express, typeorm, prisma, bullmq, passport, jwt backend, controller, service, websocket gateway
MAX_TOKENS: 7500
---

# Dev Skill — NestJS backend implementation

Implementation skill for any Dev task against a NestJS codebase. Always
follow the module layout decided by TechLead and the file-layer rules
below.

## File layer rules

- **Controller** — thin. Only decorators, DTO validation entry, guard
  annotations, and delegation to service. No business logic.
- **Service** — pure business logic. No HTTP primitives, no raw SQL.
  Inject repositories and domain services only.
- **Repository** — TypeORM/Prisma wrapper. One repository per aggregate
  root. Returns entities, not DTOs.
- **DTO** — class-validator + class-transformer. Request DTOs separate
  from Response DTOs. Never reuse entity as DTO.
- **Entity** — database shape; decorators only. No methods beyond
  trivial computed properties.

## Mandatory patterns in every new controller

```typescript
@Controller('users')
@ApiTags('users')
export class UsersController {
  constructor(private readonly users: UsersService) {}

  @Post()
  @ApiCreatedResponse({ type: UserResponseDto })
  async create(@Body() dto: CreateUserDto): Promise<UserResponseDto> {
    const user = await this.users.create(dto);
    return plainToInstance(UserResponseDto, user);
  }
}
```

- Every endpoint has Swagger `@Api*` decorators.
- Every response goes through `plainToInstance` to strip sensitive fields.

## Async side effects

- Long work (email, webhook, analytics push) → enqueue BullMQ job;
  controller returns immediately with `202 Accepted` + job id.
- External API calls → `axios` via `HttpModule` with retry + timeout;
  wrap in `@nestjs/terminus` health check if critical.

## Error handling

- Service throws `DomainException` subclasses with codes; global filter
  maps to HTTP status.
- Never catch generic `Error` in controllers.
- Every repository method has explicit "not found" semantics
  (`null` return vs `NotFoundException` — documented in the class).

## Testing expected from this skill

Each service method gets a `.spec.ts` with:
- Happy path
- Validation failure
- Repository error path
- At least one edge case relevant to the domain

Use `@nestjs/testing` `Test.createTestingModule` + in-memory repo mocks;
integration tests go to a separate `test/` folder (out of scope here).

## Common pitfalls to avoid

- Circular module imports → use `forwardRef()` only as last resort;
  prefer extracting a shared interface module.
- Transaction context lost across `await` → use AsyncLocalStorage /
  `@Transactional()` decorator, never pass `EntityManager` as arg.
- DTO `class-transformer` decorators forgotten → response leaks entity
  fields (password hash, etc.). Always apply `@Exclude()` on sensitive.
- Guard ordering — `JwtAuthGuard` before `RolesGuard`, otherwise
  `@CurrentUser()` is undefined.

## Output format

Per pipeline convention: code blocks with file path comment header, then
a short "Known limitations" bullet list at the end.
