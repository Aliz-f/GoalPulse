# Developer 3 — Phase 2 task cards

**Rule:** Read `AGENTS.md`, ADR-0003 and accepted current contracts before editing; no cross-owner edits without D1 contract PR.

## R2-D3-02 — Implement internal `checked` evidence policy (ICP-09) [P0]

**Gate:** R0/R1
**Depends on:** `R2-D1-00`
**Allowed paths:** `services/data-evidence/`

### Implementation
1. Keep HTTP-submitted observations unverified; introduce internal checker that checks HTTPS first-party merchant, exact model, price/EUR cents, timestamp/cache, stock/condition and deterministic cross-check against seller source representation; never accept caller asserted checked.
2. Store immutable audit trail for decisions and conflicts; support disputed/unverified; keep missing shipping/tax unknown for GE separately.
3. Validate first-party, freshness, contradictory, stale, forged checked and replay scenarios; D4 agrees only on semantics, not weaker GE logic.

### Acceptance criteria
- [ ] Fresh first-party independently cross-checked recorded observation may become checked through internal-only decision, with verifiable provenance; ordinary single assertion remains unverified.
- [ ] Tampered/caller checked/replayed/stale/conflicting observations never promote; conflict disputed and timestamps/audit trail intact.
- [ ] GE still blocks missing FI shipping, stock or tax; no unapproved v1 schema edit.

**Handoff / scope note:** `checked` means validated evidence at time of scrape, not guaranteed checkout or independent merchant source.

---

## R2-D3-01 — Wire real Context.dev activation→Evidence production pipeline [P0]

**Gate:** R1/R2
**Depends on:** `R2-D1-00`
**Allowed paths:** `services/discovery-monitoring/`, `services/data-evidence/`

### Implementation
1. Instantiate existing ContextProvider in main DM app/worker, never probe-only. Implement authenticated GM GoalReader and DE EvidenceSink, Rabbit activated consumer and safe source discovery.
2. Map Context search/scrape responses into exact ExtractionSubmission with provider cache/request timestamps; preserve unknown values; accept extraction failures durably.
3. Add app factory, job idempotency, bounded retry/quotas and readiness; reject fixture provenance in demo.

### Acceptance criteria
- [ ] Recorded HTTP response activation→DM job→DE immutable Observation/failure works end-to-end without test-only direct invocation; duplicate activation does not double-submit.
- [ ] A controlled actual Context.dev call via main activation path persists real source/request/time and GE sees resulting evidence.
- [ ] Missing shipping/tax/availability stays unknown; SSRF/private targets denied, no provider key logged.

**Handoff / scope note:** D1 supplies actual GM service URL and IPC; build against accepted internal API while D1 finishes runtime.

---

## R2-D3-03 — Periodic scans, operator check-now and cost guard [P0]

**Gate:** R3
**Depends on:** `R2-D3-01`
**Allowed paths:** `services/discovery-monitoring/`

### Implementation
1. Build durable due-job scheduler from existing DM job/source state; initial Search/Scrape on activation and at the user-confirmed effective interval (default 60 minutes, minimum 30).
2. Expose operator-only local check-now CLI/internal admin command using identical job logic; no new public v1 API.
3. Enforce 2 active goals per user, 3 seller URLs per goal, 100 Context calls per user/day, job dedup and skip paused/stale versions.

### Acceptance criteria
- [ ] Clock-controlled test proves first scan and second due scan, survives worker restart without double scan.
- [ ] Rate budget/quota stops excess work with visible reason; operator check-now works only with privileged local identity.
- [ ] No public unauthenticated webhook; native Context monitors are deferred P1.

**Handoff / scope note:** If user requests <30-min cadence, clarify/obtain confirmation of supported interval; never silently schedule slower.

---
