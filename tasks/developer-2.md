# Developer 2 — Goal Intelligence + Frontend

**Role:** AI/conversation and user interaction. **Owned:** `services/goal-intelligence/`, `frontend/`. **Read:** root `AGENTS.md`, GI and Frontend specs, `GoalDraft.v1`, `GoalSpec.v1`, frozen public OpenAPI, approved internal candidate spec. No direct access to GM or Context.dev database.

## Gate A — independent preparatory work

### D2-GI-01 — Draft session DB and optimistic concurrency [P0]
- **Dependencies:** frozen GoalDraft, identity design D1-PL-03; can stub principal meanwhile.
- **Allowed:** `services/goal-intelligence/`.
- **Implement:** session+messages+draft_revisions tables with per-user ownership and revision counter; create/list/get typed repositories.
- **Accept:** two parallel writes with same expected_revision yield one success and one 409; foreign session can't be read; revisions immutable.

### D2-GI-02 — LLM adapter and mock provider [P0]
- **Dependencies:** GoalDraft and model adapter design; no GM dependency.
- **Allowed:** `services/goal-intelligence/`.
- **Implement:** injected interface for structured model completion, bounded retries, timeouts, token input limit; deterministic fixture adapter and no external calls in CI.
- **Accept:** malformed model JSON, timeout, rate limit handled without marking draft ready or leaking key; mock returns reproducible normalized draft.

### D2-GI-03 — Goal compiler, required-field checker, clarification loop [P0]
- **Dependencies:** D2-GI-01/02, frozen GoalSpec.
- **Allowed:** `services/goal-intelligence/`.
- **Implement:** classify supported vs unsupported; extract candidate + explicit constraints, Decimal EUR parsing, Pydantic/JSON schema validation, calculate missing_fields, choose one next question and readiness, revision transition; no hidden defaults.
- **Accept:** product incomplete→asks shipping/condition; café incomplete→asks quantity/quality/budget; travel unsupported; no auto purchase; complete candidate convertible to GoalSpec for GM.

### D2-GI-04 — Public session endpoints and internal candidate endpoint [P0]
- **Dependencies:** D2-GI-01/03, approval ICP-01, service identity D1-PL-03.
- **Allowed:** `services/goal-intelligence/`.
- **Implement:** frozen POST session, POST message, GET session, accepted internal GET candidate exact revision/ready, idempotent mark-confirmed; response models strict.
- **Accept:** 201/200/409/404 as documented; GM consumer mock contract passes; candidate cannot be fetched without authorized GM service identity.

### D2-GI-05 — Prompt injection and conversation edge tests [P0]
- **Dependencies:** D2-GI-03/04.
- **Allowed:** `services/goal-intelligence/`.
- **Implement:** pytest cases for long prompts, malicious event/identity instructions, unsupported goals, date/currency assumptions, ambiguous units, concurrent revisions, user-approved default summary.
- **Accept:** tests pass with fake LLM; no invalid executable GoalSpec, no hidden activation, no credentials or raw model secrets in logs.

## Gate B — UI integration (parallel to D1/D3/D4 services)

### D2-FE-01 — Next.js app shell and authenticated API client [P0]
- **Dependencies:** frozen Gateway OpenAPI, D1-PL-03 identity selection.
- **Allowed:** `frontend/`.
- **Implement:** Next.js pages/components, env configuration for public gateway base URL only (no private keys), typed HTTP API client generated/hand-coded from frozen contracts, loading/errors.
- **Accept:** one command starts frontend, no secret in bundle, invalid session shows recoverable error, JWT included safely per approved auth design.

### D2-FE-02 — Conversational goal wizard and exact revision confirmation [P0]
- **Dependencies:** D2-FE-01, D2-GI-04 endpoints/mocks, D1-GM-03 confirmation interface.
- **Allowed:** `frontend/`.
- **Implement:** user message input, assistant clarifying questions, missing fields, canonical review with shown revision, explicit confirm button and success redirect.
- **Accept:** demo incomplete→clarified→confirmed works; stale 409 reloads current summary, confirm disabled for unsupported/incomplete draft; no direct frontend Context.dev calls.

### D2-FE-03 — My Goals and evaluation state UI [P1]
- **Dependencies:** D2-FE-01, D1-GM-04, ICP-02 for history detail.
- **Allowed:** `frontend/`.
- **Implement:** goal list/status/freshness and details, responsive card views; use frozen GoalSummary initially; condition breakdown uses only approved API extension or typed mock.
- **Accept:** two goals display correctly, unknown != achieved, status != purchased, no invented fulfillment percent or unsupported data; robust empty/loading/error states.

### D2-FE-04 — Notification and agent package view [P1]
- **Dependencies:** D4-NS-02, D4-AI-02, approved ICP-02 public endpoints.
- **Allowed:** `frontend/`.
- **Implement:** notification feed/read state, source links, audit timestamp, readable prepare-only package copy/view, source recheck disclaimer.
- **Accept:** one user sees own verified offer and read-only package, other user forbidden; UX does not claim order was placed; no unapproved routes merged.

## Integration handoff requirements

Provide to D1 exact session mock behavior, examples of revision conflicts, and a `GoalDraft` for each type. Provide to D4 a fully confirmed fixture `GoalSpec`, without injecting LLM text into evaluator. Deliver a UI screenshot/screencast of clarification and explicit confirm flow, plus executable browser tests.
