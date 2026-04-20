---
SCOPE: feature, module, full_app
TRIGGERS: next.js, nextjs, next, app router, server component, rsc
MAX_TOKENS: 8000
---

# Dev Skill — Next.js (App Router)

Implement feature with Next.js 14+ App Router, Server Components, Server Actions.

## Folder structure
```
app/
  <feature>/
    page.tsx              # RSC by default
    layout.tsx
    loading.tsx
    error.tsx
    components/
      <Widget>.tsx        # 'use client' only if needed
    actions.ts            # server actions with 'use server'
    schema.ts             # zod validation
src/
  lib/
    db.ts                 # prisma or drizzle
    auth.ts               # auth helper
    api-client.ts
  components/ui/          # shadcn components
```

## Convention
- Mỗi file có header: `// app/<feature>/<file>.tsx`
- Default = Server Component (no 'use client' trừ when need state/event)
- Server Actions with zod validate INPUT before when DB write
- API route handlers in `app/api/<name>/route.ts` if need JSON

## Data fetching
- Server Component: direct DB access qua prisma
- Client Component: `fetch()` into API route or tanstack-query
- Use `revalidateTag`/`revalidatePath` after mutation

## Forms
- `react-hook-form` + `zod` resolver
- Server Action nhận `FormData` + validate
- Progressive enhancement — form work no need JS

## Styling
- Tailwind + shadcn/ui components
- Dark mode qua `next-themes`

## Testing anforrs
- `data-testid="<feature>_<action>"` for interactive elements
- Playwright E2E for happy path
- Vitest unit test for server actions + validators

## Security
- `next-auth` for auth, server-side session check
- Server actions KHÔNG trust client input → always validate
- No leak env vars client-side (prefix `NEXT_PUBLIC_` explicit)
- CSP headers in `next.config.js`

## Performance
- Streaming with `<Suspense>` for slow data
- Parallel data fetching (Promise.all in RSC)
- `dynamic()` import for heavy client components
- Image qua `next/image` (lazy + size optimize)

## KHÔNG
- No lẫn server + client logic in 1 component
- No use `useEffect` to fetch — use RSC or tanstack-query
- No `'use client'` ở page.tsx trừ when bắt buộc
