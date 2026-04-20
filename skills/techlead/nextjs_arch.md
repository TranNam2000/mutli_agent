---
SCOPE: feature, module, full_app
TRIGGERS: next.js, nextjs, app router, server component, rsc, vercel
MAX_TOKENS: 8000
---

# TechLead Skill — Next.js Architecture

Architecture for Next.js 14+ App Router full-stack app.

## Tech stack
- Next.js 14+ App Router (RSC + server actions)
- TypeScript strict + eslint
- DB: **Prisma** (ORM, migrations) or **Drizzle** (SQL-first, lighter)
- Auth: **next-auth v5** (Auth.js) or **Clerk**
- Validation: **zod** (shared client+server)
- Forms: **react-hook-form** + zod resolver
- UI: **shadcn/ui** + Tailwind + Radix primitives
- State: React state / Zustand (client) — minimize client state
- Fetching: RSC default, tanstack-query only for client polling
- Queues: **BullMQ** or **Inngest** for background jobs

## Folder structure
```
app/
  (auth)/login, register, …             # route groups
  (dashboard)/...                       # another group
  api/<resource>/route.ts               # JSON API
  layout.tsx, page.tsx, error.tsx, loading.tsx
src/
  lib/
    db.ts, auth.ts, stripe.ts
  components/
    ui/ (shadcn), features/<domain>/
  server/
    actions/<domain>.ts
    services/<domain>Service.ts
    queries/<domain>Query.ts
  hooks/, utils/, types/
  middleware.ts (edge runtime)
prisma/ or drizzle/
```

## Data flow
```
User → Server Component (RSC)
   ↓ direct DB (read-only)
   ↓ render HTML
   ↓
Form submit → Server Action
   ↓ zod validate
   ↓ call service layer
   ↓ revalidatePath / revalidateTag
   ↓ return ActionResult<T>
```

## Auth & middleware
- Session check in `middleware.ts` for routes protected
- Server action tự check session again (defense in depth)
- Role-based access qua helper `requireRole('admin')`

## Testing
- Unit: Vitest + @testing-library/react for components
- Integration: Playwright for E2E critical flows
- Server actions test qua direct call + mocked session
- DB test use pg-mem or testcontainer

## CI/CD
- Vercel preview mỗi PR
- Lighthouse CI with perf budget
- `e2e-smoke` chạy trên preview URL before when merge
- Release: merge main → auto-deploy prod

## Performance
- Core Web Vitals: LCP < 2s, CLS < 0.1, INP < 200ms
- Streaming `<Suspense>` for slow data
- Edge runtime for middleware + simple APIs
- ISR for page có content update chậm (revalidate: N)
- `next/image` + `next/font` always

## Security
- CSP headers strict
- Rate limit APIs (upstash/redis)
- Input validation server-side — KHÔNG trust client
- Secrets chỉ ở server (none `NEXT_PUBLIC_` for secret)
- Logging no leak PII

## Observability
- Vercel Analytics + Speed Insights
- Sentry for error tracking
- Structured logging (pino)
- Feature flags qua env or PostHog

## KHÔNG
- No use Pages Router for project new
- No `use client` ở top-level page
- No fetch in useEffect — use RSC
- No skip zod validation in server action
