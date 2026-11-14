# 02 — Goal Intelligence Service (Developer 2)

**Ownership:** `services/goal-intelligence/`, DB `goal_intelligence`. **Criticality:** P0. **Input:** authenticated natural-language message and revision. **Output:** immutable revisioned GoalDraft + questions; only GM can activate a goal.

## Responsibilities and boundaries

- Maintain a conversational session: ask minimum necessary clarifying questions, extract structured candidate and retain transcript/revisions.
- Only two recognized executable domains: `product_purchase` and `recurring_procurement`. For travel, job search or ambiguous unsupported categories, clearly say unsupported in executable MVP; optionally preserve draft without claiming readiness.
- LLM must not issue purchases, write another service's DB, call Context.dev or autonomously activate monitoring. Do not make up a budget, product attributes, availability, prices or destination.
- Avoid over-questioning: ask one high-value blocking question at a time, group naturally related constraints when useful. Bound turns and context length; include helpful error/abandon flow.

## Public API (frozen)

| Method & path | Input | Output | Failure |
|---|---|---|---|
| POST `/v1/goal-sessions` | `{initial_message}` | 201 `GoalDraft` | 401, 422 |
| POST `/v1/goal-sessions/{session_id}/messages` | `{message,expected_revision}` | 200 updated `GoalDraft` | 401,404,409,422 |
| GET `/v1/goal-sessions/{session_id}` | session_id | 200 current `GoalDraft` | 401,404 |

Return the exact `GoalDraft.v1` shape including `missing_fields`, `next_question`, `ready_for_confirmation`, `revision` and `candidate_goal`. Client never sends `owner_user_id`.

## Internal API (ICP-01 proposal)

- `GET /internal/v1/goal-sessions/{session_id}/candidate?expected_revision=N` (GM-only) → exact current GoalDraft with verified owner/revision. Must reject stale/unready with 409; never authorize access based on UUID alone.
- `POST /internal/v1/goal-sessions/{session_id}/mark-confirmed` (GM-only, idempotent) → tracks confirmation for UX, not authoritative commit.

## Owned DB design

- `sessions(session_id PK, owner_user_id, state, current_revision, ready_for_confirmation, created_at, updated_at, expires_at)`.
- `messages(message_id PK, session_id, role [user|assistant|system], content, created_at, token_count nullable)` — redact personal details from logs; avoid storing raw chain-of-thought.
- `draft_revisions(session_id,revision,candidate_goal jsonb,missing_fields jsonb,next_question,state,created_at, PRIMARY KEY(session_id,revision))` immutable snapshots.
- `llm_attempts(id,session_id,revision,model_id,error_code,latency_ms,created_at)` metadata only, no API key or raw sensitive prompt logging.

## Draft validation algorithm

1. Classify user intent into supported type or unsupported; unknown/ambiguous stays `collecting`, not `ready`.
2. Extract candidate using constrained/structured LLM output; Pydantic validates types; local deterministic normalizer converts euros to integer cents using Decimal and explicit EUR.
3. Completeness rules for purchase: query/model policy/condition, quantity, max total EUR, delivery FI, include shipping+tax, monitoring frequency and max evidence age, safe action choice. Distinguish user-provided from inferred defaults; show defaults and require explicit approval in summary.
4. Completeness rules for recurring procurement: item name, quantity, unit, quality requirements, monthly cadence, baseline and target costs, FI delivery, shipping+tax, max sellers policy; ensure target <= baseline if task is savings. If quality claims ambiguous, ask specific question.
5. Deterministically compute `missing_fields`; enforce `GoalSpec` schema on canonical candidate **before** `ready_for_confirmation=true`; candidate may omit server-managed IDs until GM assembly.
6. `next_question` is only populated when blocking fields remain; `ready_for_confirmation=true` implies `missing_fields=[]` and next_question `null`.
7. optimistic lock: `expected_revision` must equal current revision; increment on accepted user message; 409 on conflict. Never allow LLM to overwrite revision directly.
8. On confirmation, GM rereads exact revision; UI must render canonical summary (attributes and cents converted for display), record reviewed revision; GI `mark-confirmed` best effort.

## Default / ambiguity policy

- Don't silently choose "new", "equivalent", delivery location, currency or shipping treatment if user has not accepted them.
- Configurable safe defaults (frequency 1440 min, freshness 60 min for product) are **proposals** displayed in canonical summary and included in confirmed GoalSpec. A draft may become ready only when all required constraints and selected defaults are visible to user.
- LLM nondeterminism must not cause inconsistent schema fields: retry parsing bounded, then show a recoverable error, not a fabricated spec.

## Security, throttling and prompt injection

Max input length, max session turns and rate limit; treat user prompt as data and validate schema. Never feed Context.dev fetched webpages into privileged system instructions. Do not log private messages by default beyond what user consent and storage policy permit. Internal candidate reads authenticated. Do not allow user to declare fake identity, API routes or arbitrary event type via prompt.

## Acceptance tests

- Product goal with missing shipping/condition yields missing fields and an actual follow-up; after responses passes readiness with canonical strict candidate.
- Café basket with missing quantities asks and cannot confirm; valid basket becomes ready.
- Unsupported travel goal never becomes executable under current v1.
- Concurrent stale revision returns 409 with no lost update.
- LLM malformed text, timeout or rate limit is recoverable and cannot mark ready.
- Disallowed auto-purchase request does not become `auto_purchase:true`; explicitly explain limitation.
- Cross-user session fetch and internal candidate call denied.
- Canonical result passes goal schema when assembled by GM; exact user-reviewed revision confirmed.

**Task IDs:** `D2-GI-01`..`D2-GI-05`, `D2-FE-01`..`D2-FE-04`.
