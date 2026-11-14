# 08 — Frontend + API Gateway + Identity (cross-cutting)

**Owners:** Dev 1 gateway/auth; Dev 2 frontend. These are *not* additional domain microservices. Gateway is infrastructure/edge component; frontend is browser SPA/Next.js. Domain service ownership remains the seven modules.

## Gateway responsibilities (Dev 1)

- Routes **only** versioned public `/v1` endpoints from frozen `gateway.v1.yaml` to GI or GM, with typed errors and request-size/timeouts.
- Authentication: validate externally supplied bearer JWT signature/issuer/audience/expiry and map subject → canonical UUID; inject trusted principal through private service-to-service credentials. Never trust an `X-User-Id` originating from the browser.
- Enforce CORS origin allowlist, per-user/session rate limiting, body size limits, HTTPS in deployment, secret management and request correlation ID.
- Never proxy `internal/v1` endpoints to public internet or expose Context.dev webhook receiver from another host path except explicitly allowed provider ingress.
- For P0, use a standard minimal externally-managed identity provider or agreed demo-only local dev issuer with **signed** tokens. Do not build an eighth authentication service.
- An API gateway can be an app (FastAPI reverse proxy) or vetted reverse proxy with auth middleware, but choice must be documented by Dev 1 and approved; tests must exercise route and identity verification.

## Frontend screens (Dev 2)

1. **Goal creation:** text area + send, conversation history and clarifying questions, generation error state.
2. **Review & confirm:** canonical typed summary with every user constraint and default visible; confirm the displayed `revision` using POST `.../confirm` with `approved:true`.
3. **My Goals:** list `GoalSummary` cards, lifecycle, verified opportunity state, last evaluated timestamp. Do not interpret `satisfied` as purchase completed.
4. **Goal details:** latest confirmed spec + per-condition status/evidence *only after* approved additional read interface (ICP-02). Can display a concise frozen-spec view without extension.
5. **Notifications:** in-app feed; needs approved `/v1/notifications` addition; cannot invent public endpoint in frontend.
6. **Agent handoff:** show sanitized read-only package and a copy action; needs approved public package read API. Explicitly show "not ordered" and source freshness.

## UI acceptance criteria

- User cannot activate without clicking confirm on exact current revision; server 409 stale updates prompt reload.
- If an LLM draft is unsupported or incomplete, show question/unsupported explanation, no confirm action.
- Privacy: only own goals/notifications/packages visible; switching accounts clears client cache.
- Accessible form labels, keyboard confirmation, mobile layout, loading/empty/error states, internationalized amount EUR and UTC/user-local display without changing source timestamps.
- The UI shows missing/unknown evidence as unknown, not a green completion bar. No false live-price claims.

## Public endpoint gap

Currently `gateway.v1.yaml` exposes 7 operations: session create/message/get/confirm and goals list/get/lifecycle; it **does not** include notification read, evaluation history, or package read. ICP-02 must be approved before implementing those. Until then, build UI with adapters for fixture/mocked responses, and do not merge undocumented gateway routes into `main`.

## Integration tests

- Browser: first draft → clarification → canonical review → click confirm → active goal in list.
- JWT missing/invalid ⇒ 401; user B cannot access user A's goal.
- Stale session revision ⇒ 409 with refetch UX.
- Gateway has no direct connection to PostgreSQL from another service and no access to Context.dev API key.
- Notification/package screens only function against signed-off API or explicit mock mode; no silently invented endpoints.

**Tasks:** `D1-GW-*` and `D2-FE-*` in task files.
