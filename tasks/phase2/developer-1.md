# Developer 1 — Phase 2 task cards

**Rule:** Read `AGENTS.md`, ADR-0003 and accepted current contracts before editing; no cross-owner edits without D1 contract PR.

## R2-D1-00 — Apply decided MVP policies and additive contract PR [P0]

**Gate:** R0
**Depends on:** None
**Allowed paths:** `docs/adr/`, `docs/implementation/DECISIONS.md`, `contracts/`, `AGENTS.md`

### Implementation
1. Import ADR-0003; reconcile against accepted ADR-0002 and current code on actual branch. Record all MVP/ICP decisions in repo decision log; freeze v1 files.
2. Publish additive read contracts for evaluations/notifications and isolated versioned demo session; treat grouped seller_orders ActionPackage as P1 and derive shape from existing D4 code.
3. Add AGENTS.md Phase-2 addendum and contract tests; do not silently edit any existing accepted v1 route or Event name.

### Acceptance criteria
- [ ] ADR and decision log merged, with no modifications to frozen v1; new extensions have ownership/auth and strict schemas.
- [ ] One tested owner-to-owner denial; D2 and D4 get URL/DTO handoff; no direct public internal service route.
- [ ] Existing 5 schemas/4 events/7 frozen Gateway paths still validate.

**Handoff / scope note:** Coordinate shared files; others never edit them before this PR.

---

## R2-D1-01 — Runnable Gateway, demo authentication and Compose stack [P0]

**Gate:** R1
**Depends on:** `R2-D1-00`
**Allowed paths:** `infrastructure/gateway/`, `infrastructure/docker-compose.yml`, `infrastructure/`, `docs/phase2/`

### Implementation
1. Implement all seven frozen public paths on Gateway with error/status preservation and accepted ICP-08 service JWTs.
2. Build isolated demo auth endpoint from versioned extension, two preprovisioned users, short-lived HttpOnly cookie with Origin/CSRF protection; reject missing config and keep services private.
3. Add production app/worker runtime commands or Dockerfiles for P0 services (Goal Intelligence, Goal Management, Discovery & Monitoring, Data & Evidence, Goal Evaluation, Notification); Agent Integration is enabled when P1 is completed (with owners verifying packages), per-service migrations, readiness, health, env fail-closed checks.
4. Apply request size/time limits, CORS scope, safe secret handling; add single documented startup command.

### Acceptance criteria
- [ ] One documented command boots disposable DB/Rabbit, Gateway, 7 service runtimes and workers; readiness green, no direct public service endpoints.
- [ ] All 7 frozen Gateway routes are wired and tested with fake downstream; two-user cross-owner denial and unauthenticated access fail.
- [ ] Demo refuses FE mock/GI fixture/DE fixture config and missing secrets; no token paste.

**Handoff / scope note:** Publish Gateway and auth integration contract for D2 within first PR; make other developers own their runtime builder implementation when necessary.

---

## R2-D1-02 — Goal Management production runtime and truthful projection [P0]

**Gate:** R1
**Depends on:** `R2-D1-01`
**Allowed paths:** `services/goal-management/`

### Implementation
1. Bind existing injected repository, GI candidate reader, outbox publisher and GE evaluated consumer to a runnable process.
2. Complete idempotent goal activation, DB migration and /ready; read current goal-version evaluation projection in list/detail instead of hard-coded not_found.
3. Publish exact owner/goal current-version read for D3/D4; wire service identity and retries.

### Acceptance criteria
- [ ] GI exact ready revision confirmation persists through restart and emits activation once under replay.
- [ ] Goal list/detail shows the actual current evaluation result after GE event, and ignores stale version events.
- [ ] Cross-owner reads/confirmation fail and tests prove immutable versions.

**Handoff / scope note:** No new GoalSpec fields or v1 events.

---

## R2-D1-03 — Full-stack integration, outages, security and release evidence [P0]

**Gate:** R3/R4
**Depends on:** `R2-D1-02`, `R2-D2-01`, `R2-D2-02`, `R2-D2-04`, `R2-D3-01`, `R2-D3-02`, `R2-D3-03`, `R2-D4-01`, `R2-D4-02`
**Allowed paths:** `infrastructure/`, `tests/`, `docs/phase2/`, `.github/workflows/`

### Implementation
1. Connect recorded-provider E2E Browser→GI→GM→Rabbit→DM→DE→GE→NS with isolated DBs.
2. Test duplicate messages, stale versions, pause, Rabbit worker/DB restart, provider 429/5xx, DLQ/retry and cross-owner access.
3. Create redacted release report with actual commands, current commit, PASS/FAIL/NOT RUN; run separately authorized live Anthropic + Context main-flow smoke.

### Acceptance criteria
- [ ] All P0 release scenarios A1–D5 pass or release is marked blocked with exact reproducible exceptions.
- [ ] One no-silent-mock deployment test and one live safe main-path proof; no fabricated Verified.
- [ ] No checkout/payment/booking calls, no secrets/raw goals in diagnostic artifacts.

**Handoff / scope note:** Integration owner; do not silently fix cross-owner source code; send scoped tickets.

---
