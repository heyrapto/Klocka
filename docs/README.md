# Frontend Application — Next.js 15+ App Router

> **Notice:** The Next.js installation for this folder is reserved for the user as requested.

## Planned Frontend Structure
When you install Next.js into this folder (e.g., using `pnpm create next-app . --typescript --tailwind --eslint --app --src-dir`), adhere to the architectural guidelines outlined in:
- [`docs/DESIGN_DIRECTION.md`](file:///Users/mac/prediction-market/docs/DESIGN_DIRECTION.md)
- [`docs/TECH_STACK.md`](file:///Users/mac/prediction-market/docs/TECH_STACK.md)
- [`docs/REALTIME_PROTOCOL.md`](file:///Users/mac/prediction-market/docs/REALTIME_PROTOCOL.md)
- [`docs/ARCHITECTURE.md`](file:///Users/mac/prediction-market/docs/ARCHITECTURE.md)

## Recommended Setup Commands
```bash
cd apps/web
pnpm create next-app . --typescript --tailwind --eslint --app --no-src-dir
pnpm add @tanstack/react-query @supabase/ssr @supabase/supabase-js zod lucide-react
pnpm add -D @types/node @types/react @types/react-dom
```

## Architectural Guidelines for the Frontend
1. **Server Components by Default:** Use React Server Components (RSC) for read-heavy displays (forecast detail, historical logs, resolution terms).
2. **Client Components Only When Interactive:** Reserve `"use client"` for file upload drag-and-drop, interactive SSE event streaming, probability charts, and feedback modals.
3. **Strict Zero-Any Policy:** TypeScript strict mode enabled with zero tolerance for `any`.
4. **Institutional Cockpit Styling:** Dark theme (`zinc-950`), WCAG 2.1 AA accessible contrast ratios ($> 4.5:1$), and **never display a green badge for `NO_TRADE`** (use neutral zinc/slate).
