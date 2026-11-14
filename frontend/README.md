# GoalPulse frontend (Developer 2)

Next.js 16 app for **D2-FE-01** (app shell + typed API client), **D2-FE-02** (conversational goal wizard + exact-revision confirmation), **D2-FE-03** (My Goals) and **D2-FE-04** (notifications + prepared purchase view, mock-only until ICP-02). Owned path: `frontend/`.

## Run

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000, mock mode by default
npm test               # unit tests (node:test, no extra deps)
npm run e2e            # Playwright browser tests in mock mode (starts its own dev server on :3100)
npm run typecheck && npm run lint
```

## Modes

Copy `.env.example` to `.env.local` and pick one:

| `NEXT_PUBLIC_API_MODE` | Behaviour |
|---|---|
| `mock` (default) | In-browser replay of conversations **recorded from the real Goal Intelligence service**. Your text is not interpreted; each reply advances the script (a "Scripted reply" chip shows the next one). Labelled "Mock mode" in the header. "Simulate an edit from another tab" forces the next send/confirm to get HTTP 409. Sessions live in memory, so a page refresh loses them. |
| `gateway` | Calls the public gateway at `NEXT_PUBLIC_GATEWAY_URL` (frozen `contracts/openapi/gateway.v1.yaml`) with `Authorization: Bearer <token>`. Until ICP-08 picks the identity provider, paste a dev token in the header; it's kept in memory + `sessionStorage` only and clears all cached drafts on sign-out or account change. |

Only `NEXT_PUBLIC_*` values reach the browser, and they hold no secrets. The frontend never calls Goal Intelligence, Goal Management or Context.dev directly, only the gateway.

## Screens

- `/`: describe a goal (with examples) and start a session (`POST /v1/goal-sessions`).
- `/sessions/[sessionId]`: conversation with one clarifying question at a time, "still needed" fields, and a live draft summary marking each value as from you, proposed default, or always (no automatic buying). When `ready_for_confirmation`, a **Review and confirm** panel confirms exactly the shown revision (`POST …/confirm` with `{expected_revision, approved: true}`).
- `/goals` (D2-FE-03): your goals as cards (filter by lifecycle), each with the frozen `GoalSummary` status and how long ago it was checked. Wording keeps "Not enough evidence" distinct from success, and "Verified offer found" says nothing was bought. No progress percentages or condition breakdowns (those need ICP-02).
- `/goals/[goalId]`: the approved GoalSpec, status, and Pause / Resume / Mark as done / Cancel (`PATCH /v1/goals/{id}/lifecycle`). Ending actions need a second click; the goal is re-read after every action.
- `/notifications` and `/packages/[packageId]` (D2-FE-04, **mock mode only**): verified-offer feed with unread state, timestamps (local, UTC on hover) and mark-as-read; read-only prepared purchase with "Not ordered" and "re-check on the seller's site" notices, safe source links (http/https only), missing shipping/tax shown as "Not stated" (never 0), expired packages (410) shown as expired, and copy as text / JSON. In gateway mode these screens explain that ICP-02 is pending and make no calls.
- **Stale revision (409):** the latest draft is reloaded, a warning explains what happened, and the typed message stays in the box. Confirm is not offered for incomplete or unsupported drafts.

## Icons

All icons are [Lucide](https://lucide.dev) (ISC licence) inline SVGs via `lucide-react`, wrapped in `src/components/Icons.tsx`. They render identically on every OS. Don't use SF Symbols: Apple's licence limits them to apps on Apple platforms. Icons are decorative (`aria-hidden`); the text always carries the meaning.

## Code map

| Path | Purpose |
|---|---|
| `src/lib/contracts.ts` | TS mirrors of GoalDraft.v1 / GoalSpec.v1 |
| `src/lib/api/gateway.ts` | Typed client for the 4 frozen session/confirm operations |
| `src/lib/api/mock.ts`, `mock-scripts.ts` | Mock API; scripts generated from `services/goal-intelligence` |
| `src/lib/api/errors.ts` | `ApiError` + one place deciding message / keep input / reload per failure |
| `src/lib/summary.ts` | Defensive rendering of the untrusted `candidate_goal` (absent → "Not set yet") |
| `src/lib/auth.ts` | Interim token store (ICP-08 pending) |
| `src/lib/goals.ts` | Status/lifecycle wording, transition matrix, freshness text |
| `e2e/` | Playwright specs: wizard → confirm, 409 on send and confirm, unsupported, My goals, lifecycle, notifications, packages |
| `src/components/` | `AppHeader`, `NewGoalForm`, `SessionView`, `DraftSummary`, `GoalsList`, `GoalDetail`, `GoalStatus` |

## Not done yet

- D2-FE-04 has no gateway client on purpose (`src/lib/api/proposed.ts`); add it in the PR that approves ICP-02.
- Mock mode seeds two sample goals (one verified, one lacking evidence) so every status style is visible; disable with `createMockApi({ seedGoals: false })`.
- Real sign-in once ICP-08 is decided.

## ICP-02 feedback from the frontend (for D1 + D4)

1. `Notification` has no link to its package. Notification's own table has `action_package_id nullable` (docs/implementation/06-notification.md), so please expose `action_package_id: string | null` in the public schema. The mock uses that name (marked in `src/lib/proposed.ts`).
2. The NS acceptance tests require an evidence link and an "offer may change at checkout" note in each notification, but the public `Notification` has neither. Either add `source_url` (or rely on the package), or confirm the frontend's fixed disclaimer text is enough.
3. `ActionPackage.seller_offers[].quantity` is a decimal string while GoalSpec uses numbers; fine for display, just confirm it's intentional.
