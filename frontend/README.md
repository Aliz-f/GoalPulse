# GoalPulse frontend (Developer 2)

Next.js 16 app for **R2-D2-02** (real gateway client, demo cookie sign-in, fail-closed runtime), **D2-FE-01** (app shell + typed API client), **D2-FE-02** (conversational goal wizard + exact-revision confirmation), **D2-FE-03** (My Goals) and **D2-FE-04** (notifications + prepared purchase view, mock-only until ICP-02). Owned path: `frontend/`.

## Run

Requires **Node 22** (`frontend/.nvmrc`, ADR-0003 MVP-02). Every npm script first runs `scripts/check-node.mjs`, which stops with install instructions on another major version; reinstall with `npm ci` after switching. `npm run e2e` starts its own `next dev` on :3100 with a separate build folder (`.next-e2e`), so it works while `npm run dev` is running.

```bash
cd frontend
npm install
cp .env.example .env.local   # required: there is no default mode (see Modes)
npm run dev            # http://localhost:3000
npm test               # unit tests (node:test, no extra deps)
npm run e2e            # Playwright, mock lane (own dev server on :3100)
npm run e2e:gateway    # Playwright, gateway lane: demo profile + cookie sign-in (dev server :3101, stub gateway :8100)
npm run demo           # handoff screencast: demo-output/*.png steps + *.webm videos (git-ignored)
npm run typecheck && npm run lint
```

## Modes

Three settings, all required (`.env.example`). There are **no defaults**: a missing or unknown value stops `next dev` / `next build` with a message, and the app re-checks on load, so a build can never quietly fall back to the mock (R2-D2-02, ADR-0003 MVP-01).

| `NEXT_PUBLIC_GOALPULSE_PROFILE` | `NEXT_PUBLIC_API_MODE` | Behaviour |
|---|---|---|
| `test` / `dev` | `mock` | In-browser replay of conversations **recorded from the real Goal Intelligence service**. Your text is not interpreted; each reply advances the script (a "Scripted reply" chip shows the next one). Labelled "Mock mode" in the header. "Simulate an edit from another tab" forces the next send/confirm to get HTTP 409. No accounts. Sessions live in memory, so a refresh loses them. |
| `test` / `dev` / `demo` | `gateway` | Calls the public gateway at `NEXT_PUBLIC_GATEWAY_URL` (an origin, e.g. `http://localhost:8000`). Sign-in is the demo cookie flow below. |
| `demo` | `mock` | **Refused.** The demo only runs against the real gateway. |

### Sign-in in gateway mode (ADR-0003 MVP-06)

- The browser posts username + password once to `POST /v2/demo/sessions` (`contracts/openapi/gateway.phase2.extension.v1.yaml`). The gateway answers with the non-secret `DemoUser` and sets the **HttpOnly** `goalpulse_session` cookie. The password field is cleared after every attempt.
- Every call uses `credentials: "include"`. The browser **never** holds, stores or sends a token, sends no `Authorization` header and never supplies an owner ID; nothing goes to `localStorage`/`sessionStorage`. On load (refresh or browser restart) the app asks `GET /v2/demo/session` who is signed in.
- Pages render only for a signed-in user and remount when the account changes; cached drafts are dropped on sign-out or account switch. Any `401` shows "Your session ended. Sign in again."
- Sign-out posts `/v2/demo/sessions/logout`; if that fails the UI stays signed in rather than pretending.
- Sign-in errors: 401 wrong credentials, 403 origin/CSRF refused, 429 too many attempts, 503 demo sign-in disabled, network.
- The New goal form says that goal text is sent to Anthropic's Claude (MVP-05).

**Needs from D1's gateway (R2-D1-01):** CORS for the frontend origin with `Access-Control-Allow-Credentials: true` (exact origin, not `*`) and `Accept, Content-Type, X-Correlation-ID` allowed; the page and the gateway on the same site (e.g. both `localhost`) so the `SameSite=Lax` cookie is sent. CSRF: the frontend relies on the browser's `Origin` header today. If D1 picks a CSRF token header, it is added in one place, `src/lib/api/http.ts`.

### Checks for this

- `tests/routes.test.ts`: only approved routes; no browser storage, `Authorization` header or `credentials: "omit"` in `src/`; only the three `NEXT_PUBLIC_*` settings are read.
- `scripts/check-bundle.mjs` (CI, after each build): the browser bundle holds no keys, secret settings, JWTs, private service routes or the old token store.
- CI also proves a demo + mock build and a build with no settings both **fail**.
- `npm run e2e:gateway`: sign-in, wrong password, reload and browser-restart persistence, two-account isolation (incl. cross-owner 404), no credentials visible to page scripts. Until R2-D1-01 lands it runs against `e2e/gateway/stub-gateway.mjs`, a **contract test double** that proves the frontend half only. Against the real gateway: `GOALPULSE_E2E_GATEWAY_URL=http://127.0.0.1:8000 GOALPULSE_E2E_USER_A=… GOALPULSE_E2E_PASS_A=… GOALPULSE_E2E_USER_B=… GOALPULSE_E2E_PASS_B=… GOALPULSE_E2E_GOAL_A="<A's goal title>" GOALPULSE_E2E_GOAL_B="<B's goal title>" npm run e2e:gateway`.

Only `NEXT_PUBLIC_*` values reach the browser, and they hold no secrets. The frontend never calls Goal Intelligence, Goal Management, Anthropic or Context.dev directly, only the gateway.

## Screens

- `/`: describe a goal (with examples) and start a session (`POST /v1/goal-sessions`).
- `/sessions/[sessionId]`: conversation with one clarifying question at a time, "still needed" fields, and a live draft summary showing every candidate constraint. User facts are marked "From you"; values whose provenance is not present in frozen GoalDraft are neutrally marked "In this draft" instead of being mislabeled as defaults. When `ready_for_confirmation`, a **Review and confirm** panel confirms exactly the shown revision (`POST …/confirm` with `{expected_revision, approved: true}`).
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
| `src/lib/runtime.ts` | Fail-closed profile/mode/gateway URL selection |
| `src/lib/api/http.ts` | The one request path: cookie credentials, correlation ID, ICP-01 errors, 401 → sign in |
| `src/lib/api/gateway.ts` | Typed client for the frozen session/confirm and goal operations |
| `src/lib/api/demo-auth.ts`, `src/lib/session.ts` | Demo sign-in client and the signed-in-user store |
| `src/components/AuthGate.tsx` | Sign-in screen; renders pages only for a signed-in user |
| `src/lib/api/mock.ts`, `mock-scripts.ts` | Mock API; scripts generated from `services/goal-intelligence` |
| `src/lib/api/errors.ts` | `ApiError` + one place deciding message / keep input / reload per failure |
| `src/lib/summary.ts` | Defensive rendering of the untrusted `candidate_goal` (absent → "Not set yet") |
| `src/lib/goals.ts` | Status/lifecycle wording, transition matrix, freshness text |
| `e2e/` | Playwright specs: wizard → confirm, 409 on send and confirm, unsupported, My goals, lifecycle, notifications, packages |
| `src/components/` | `AppHeader`, `NewGoalForm`, `SessionView`, `DraftSummary`, `GoalsList`, `GoalDetail`, `GoalStatus` |

## Not done yet

- D2-FE-04 has no gateway client on purpose (`src/lib/api/proposed.ts`); add it in the PR that approves ICP-02.
- Mock mode seeds two sample goals (one verified, one lacking evidence) so every status style is visible; disable with `createMockApi({ seedGoals: false })`.
- Real-gateway acceptance for R2-D2-02 (persistent goal after restart, two-user isolation) runs once R2-D1-01 lands; see "Checks for this".
- Notifications in gateway mode: R2-D2-04 (Phase 2 extension API).

## ICP-02 feedback from the frontend (for D1 + D4)

1. `Notification` has no link to its package. Notification's own table has `action_package_id nullable` (docs/implementation/06-notification.md), so please expose `action_package_id: string | null` in the public schema. The mock uses that name (marked in `src/lib/proposed.ts`).
2. The NS acceptance tests require an evidence link and an "offer may change at checkout" note in each notification, but the public `Notification` has neither. Either add `source_url` (or rely on the package), or confirm the frontend's fixed disclaimer text is enough.
3. `ActionPackage.seller_offers[].quantity` is a decimal string while GoalSpec uses numbers; fine for display, just confirm it's intentional.
