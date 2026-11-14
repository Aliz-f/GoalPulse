# GoalPulse frontend (Developer 2)

Next.js 16 app for **D2-FE-01** (app shell + typed API client) and **D2-FE-02** (conversational goal wizard + exact-revision confirmation). Owned path: `frontend/`.

## Run

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000, mock mode by default
npm test               # unit tests (node:test, no extra deps)
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
- **Stale revision (409):** the latest draft is reloaded, a warning explains what happened, and the typed message stays in the box. Confirm is not offered for incomplete or unsupported drafts.

## Code map

| Path | Purpose |
|---|---|
| `src/lib/contracts.ts` | TS mirrors of GoalDraft.v1 / GoalSpec.v1 |
| `src/lib/api/gateway.ts` | Typed client for the 4 frozen session/confirm operations |
| `src/lib/api/mock.ts`, `mock-scripts.ts` | Mock API; scripts generated from `services/goal-intelligence` |
| `src/lib/api/errors.ts` | `ApiError` + one place deciding message / keep input / reload per failure |
| `src/lib/summary.ts` | Defensive rendering of the untrusted `candidate_goal` (absent → "Not set yet") |
| `src/lib/auth.ts` | Interim token store (ICP-08 pending) |
| `src/components/` | `AppHeader`, `NewGoalForm`, `SessionView`, `DraftSummary` |

## Not done yet

- D2-FE-03 (My Goals) and D2-FE-04 (notifications / agent package) are next. FE-04 needs the ICP-02 public endpoints approved.
- Browser end-to-end tests: Playwright isn't installed in the repo yet.
- Real sign-in once ICP-08 is decided.
