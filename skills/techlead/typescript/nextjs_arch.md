---
SCOPE: feature, module, full_app
TRIGGERS: nextjs, next.js, react app, web app, ssr, ssg, app router, server component
MAX_TOKENS: 4000
---

# TechLead Skill — Next.js Web Architecture

## Output bắt buộc

### 1. Folder structure (App Router)
```
src/
  app/
    (marketing)/...
    (dashboard)/...
    api/<route>/route.ts
    layout.tsx
  components/{ui,forms,layout}/
  lib/{db, auth, validation}/
  hooks/
  types/
  styles/
```

### 2. Rendering strategy per route
- SSG: marketing pages, blog
- SSR: dashboard, user-specific
- ISR: catalog with revalidate window
- Client component: interactive widget
- Quy tắc: bắt đầu với Server Component, opt-in `"use client"` khi cần state/effect

### 3. Data fetching
- Server-side: `fetch()` với Next caching tags
- Client-side: SWR / React Query với optimistic update
- DB: Prisma trong API routes / RSC

### 4. Auth
- NextAuth.js v5 (Auth.js) — providers + middleware-based session
- Protect route bằng `middleware.ts` + redirect

### 5. Performance budgets
- TTFB < 200ms (SSR)
- LCP < 2.5s
- Bundle JS first-load < 150kb
- Image: `next/image` + AVIF/WebP

### 6. Testing
- Vitest + React Testing Library cho component
- Playwright cho E2E

---

<!-- TOOL-USE-HINT v1 -->
### 🛠 Working in the project

You run **inside the user's project directory** — the claude CLI has native `Read` / `Glob` / `Grep` / `Edit` / `Write` / `Bash`.

Use `Glob` / `Grep` / `Read` to survey the actual codebase first. Use `Edit` / `Write` to save architecture docs into `docs/arch/<feature>.md`. Echo task assignment + sprint plan in the reply.
