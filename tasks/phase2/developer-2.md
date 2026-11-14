# Developer 2 — Phase 2 task cards

**Rule:** Read `AGENTS.md`, ADR-0003 and accepted current contracts before editing; no cross-owner edits without D1 contract PR.

## R2-D2-03 — Fix Node 22 frontend build and Playwright startup [P0]

**Gate:** R0
**Depends on:** None
**Allowed paths:** `frontend/`

### Implementation
1. Reproduce Next.js TypeScript --showConfig parsing error on Node 22; fix root cause without skipping TypeScript validation.
2. Fix Playwright webServer startup and ensure mock lane stays explicitly labeled.
3. Make CI fail if `npm run build` or Playwright fails.

### Acceptance criteria
- [ ] Under Node 22: npm ci, typecheck, lint, test, build, e2e all exit 0.
- [ ] No deletion or skipping of failed tests; FE production build can select real Gateway.

**Handoff / scope note:** Independent immediate task.

---

## R2-D2-01 — Real Anthropic LLM and safe fail-closed Goal Intelligence [P0]

**Gate:** R1
**Depends on:** `R2-D1-00`
**Allowed paths:** `services/goal-intelligence/`

### Implementation
1. Reuse existing `llm/anthropic.py` and injection interface; select GI_LLM_PROVIDER=anthropic in demo, model from actual API entitlement; keep fixture only in explicit test/dev.
2. Validate model JSON into GoalDraft/revision; dynamic questions from user input, strict missing fields; never auto-activate.
3. Visible provider failure, timeouts, bounded retries/rate and privacy disclosure; reconcile extractor-failure revision semantics with docs.

### Acceptance criteria
- [ ] Controlled single non-sensitive live API call yields a distinct dynamic clarifying question and valid Draft; replaying different goal changes question.
- [ ] Demo refuses fixture, missing Anthropic key/model; timeout/malformed output cannot confirm or silently invoke fake provider.
- [ ] GI Python3.12 tests and injection/ownership tests pass; no secrets/private goal text logged.

**Handoff / scope note:** D1 Gateway not needed for standalone provider smoke; coordinate config but keep service ownership.

---

## R2-D2-02 — Real browser Gateway client and session handling [P0]

**Gate:** R1/R2
**Depends on:** `R2-D1-01`, `R2-D2-03`
**Allowed paths:** `frontend/`

### Implementation
1. Make `gateway` mode required in demo build; unknown/missing mode must fail instead of loading in-memory mock.
2. Use D1 cookie auth/session API; remove pasted JWT from real sessionStorage path; no public credential in Next bundle.
3. Connect Draft, confirm, goal list/detail and pause UI to real Gateway with typed DTOs and proper 401/404/409/retry states.

### Acceptance criteria
- [ ] Browser to authenticated Gateway displays persistent goal after refresh/restart; no fake sample card appears.
- [ ] Two-user isolation demonstrated; production/demo build cannot default to mock or fixture.
- [ ] No new unsupported UI public endpoint; request/response shapes match approved contract.

**Handoff / scope note:** User-facing entry point; never call Anthropic/Context from browser.

---

## R2-D2-04 — Truthful My Goals and notification views [P0]

**Gate:** R3
**Depends on:** `R2-D2-02`, `R2-D4-02`
**Allowed paths:** `frontend/`

### Implementation
1. Render actual GE results and condition-level unknown/missing reasons from current-version read, including source and observed_at.
2. Implement owner-only notification listing/read with approved additive API; show distinct Candidate vs Verified and active vs completed.
3. Show last attempted/successful check and quota/cost caveat; no fake fulfillment percentage or fabricated global coverage.

### Acceptance criteria
- [ ] Recorded unknown status renders Unknown and source/timestamp, not Verified; verified scenario renders one notification.
- [ ] After browser restart still displays stored data; no mock path or seed data in demo.
- [ ] Auth errors, empty state, provider unavailable and cross-owner denial handled.

**Handoff / scope note:** If package P1 unavailable, hide button rather than display fake package.

---
