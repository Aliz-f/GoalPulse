# GoalPulse Phase 2 Readiness Audit

## 1. **Audit Metadata and Scope**

| Item | Result |
|---|---|
| Audit date | 2026-11-14 (Europe/Helsinki) |
| Repository root | `/home/alireza/aliz_projects/goal-pulse` |
| Branch | `main` |
| Audited commit | `5186edf` |
| Upstream state | `[OBSERVED]` `main...origin/main`, no ahead/behind indication after fetch/pull |
| Pre-existing dirty state | `[OBSERVED]` `.github/workflows/contracts.yml` modified before the audit; preserved and not included in this audit's changes |
| Audit writes | These two report artifacts only, under `docs/audits/` |

This is a source and offline-test readiness audit, not a deployment certification. `[OBSERVED]` The implementation has advanced beyond the historical mock-only blueprint in several areas: Goal Intelligence now contains an optional Anthropic adapter; Discovery & Monitoring contains a real Context.dev HTTP adapter; and all seven services contain meaningful domain or persistence code. `[OBSERVED]` The executable product still defaults to a frontend mock and lacks the Gateway, process wiring, and cross-service adapters needed for a real end-to-end run.

Evidence labels used throughout:

- `[OBSERVED]`: directly verified in repository source, configuration, or a command executed during this audit.
- `[INFERRED]`: conclusion derived from multiple observed facts, with the basis stated.
- `[UNVERIFIED]`: implementation or behavior could not be demonstrated locally without external systems or credentials.
- `[NOT RUN]`: a specific test or operation was deliberately not executed, with the reason recorded.

Scope included `AGENTS.md`, `CLAUDE.md`, root and service READMEs, architecture/data-model/ADR/implementation/task documents, frozen and proposed contracts, service and frontend source, migrations, Compose, RabbitMQ definitions, CI workflows, environment-example key names, and safe offline tests. No provider call, live LLM call, container startup, migration, database mutation, email/payment/booking call, or production request was made. Existing `.env` values were neither read nor printed.

## 2. **Executive Summary**

**Overall verdict: `BLOCKED` for a truthful live Phase 2 demo; `PARTIAL` as a mock-first, unit-tested implementation foundation.**

`[OBSERVED]` The repository contains robust domain logic and 717 passing backend tests outside Goal Intelligence, plus passing frontend type, lint, and unit checks. `[OBSERVED]` It does not contain an executable full stack: Compose starts only PostgreSQL, RabbitMQ, and RabbitMQ initialization; the Gateway exposes only `/health/live`; several service apps require injected objects with no production builder; and the frontend defaults to in-memory mock mode. `[INFERRED]` No current configuration can carry a user request from the browser through a real provider, durable evidence, evaluation, notification, and handoff.

Top five P0 blockers, in critical-path order:

1. **Gateway and application deployment path are absent.** The Gateway has no public contract routes or proxy behavior, and Compose has no application services (`infrastructure/gateway/goalpulse_gateway/app.py:1-36`, `infrastructure/docker-compose.yml:1-60`).
2. **Cross-service runtime wiring is incomplete.** Discovery, Evaluation, Notification, and Agent Integration depend on injected protocol implementations; concrete HTTP readers, RabbitMQ consumers, and process entry points are missing from the main path (`services/discovery-monitoring/goalpulse_discovery/workflow.py:30-39`, `services/goal-evaluation/goal_evaluation/processing.py:1-69`, `services/notification/notification/consumer.py:39-49`).
3. **The evidence trust chain cannot reach `checked`.** Data & Evidence accepts `checked` input but maps every non-disputed submission to `unverified`, while Evaluation, Notification, and Agent Integration require checked evidence for verification (`services/data-evidence/goalpulse_evidence/api.py:28,119-125`, `services/goal-evaluation/goal_evaluation/evaluators/product.py:160-173`). ICP-09 is still pending.
4. **The real Context.dev client is not used by the application workflow.** It is instantiated by a separate probe only; the production `GoalReader` and `EvidenceSink`, activation consumer, provider-to-extraction mapping, monitor scheduler, and webhook HTTP ingress are not bound (`services/discovery-monitoring/goalpulse_discovery/probe.py:33-60`, `services/discovery-monitoring/goalpulse_discovery/workflow.py:30-137`, `services/discovery-monitoring/goalpulse_discovery/webhooks.py:1-139`).
5. **The browser path remains mock-first and the real build path is not green locally.** `NEXT_PUBLIC_API_MODE` defaults to mock; proposed notification/package reads are mock-only; the Gateway client has no working Gateway behind it; and `npm run build` exited 1 after compilation (`frontend/src/lib/api/index.ts:7-17`, `frontend/src/lib/api/proposed.ts:4-11`).

The concrete next action is Gate R0: approve the still-pending ICP decisions needed for evidence reliability and notification/package reads, then land R2-D1-01 (Gateway/stack skeleton), R2-D3-01 and R2-D3-02 (live discovery/evidence path), and R2-D4-01 (Evaluation worker) behind a no-silent-mock integration test. Do not advertise or demonstrate “live” mode until Gate R3 passes.

## 3. **Repository and Runtime Inventory**

### Platform inventory

| Area | Observed implementation |
|---|---|
| Python | Policy and CI target Python 3.12+; audit host exposed Python 3.11.5, which blocks Goal Intelligence test collection |
| Backend | FastAPI, Pydantic v2, Uvicorn; per-service requirements files |
| Frontend | Next.js 16.4.6, React 19.3, TypeScript 5; npm with `package-lock.json`; CI Node 22 (`frontend/package.json`, `.github/workflows/frontend.yml:27-40`) |
| Persistence | PostgreSQL 16 in Compose; separate service database credentials are declared; migrations exist for all seven services |
| Messaging | RabbitMQ 4 management image, topic exchange `goalpulse.events`; durable quorum queues, DLQs, and exact route bindings are declared (`infrastructure/rabbitmq/definitions.json:4-29`) |
| Containers | `[OBSERVED]` No Dockerfiles, Kubernetes manifests, or Helm charts. Compose contains PostgreSQL, RabbitMQ, and `rabbitmq-init` only (`infrastructure/docker-compose.yml:1-60`) |
| CI | Contract/root tests, frontend checks in mock mode, and Goal Intelligence PostgreSQL tests. No full-stack, live-provider, D3/D4 worker, or broker integration workflow (`.github/workflows/contracts.yml:1-21`, `.github/workflows/frontend.yml:21-65`, `.github/workflows/goal-intelligence.yml:15-39`) |

### Components and executable surfaces

| Component | Directory | Entrypoint/startup evidence | Runtime state |
|---|---|---|---|
| Goal Intelligence | `services/goal-intelligence/` | `uvicorn goal_intelligence.api.app:create_app --factory --port 8102` (`services/goal-intelligence/README.md:30-42`) | Standalone app can be built with configured DB; defaults to fixture LLM |
| Goal Management | `services/goal-management/` | `create_app(reads, candidates, ...)` requires injected dependencies (`services/goal-management/src/goal_management/api.py:179-308`) | No production app factory/process wiring |
| Discovery & Monitoring | `services/discovery-monitoring/` | API factory and separate live probe; no main worker builder | Core plus real provider adapter, not a runnable workflow |
| Data & Evidence | `services/data-evidence/` | API factory takes ledger/read dependencies (`services/data-evidence/goalpulse_evidence/api.py:64-149`) | Core/API present; no production process/publisher startup |
| Goal Evaluation | `services/goal-evaluation/` | Processing module explicitly describes mock-adapter orchestration (`services/goal-evaluation/goal_evaluation/processing.py:1-5`) | Pure/durable core; no HTTP or broker process |
| Notification | `services/notification/` | No runnable HTTP/broker app (`services/notification/README.md:3-13`) | Durable local core only |
| Agent Integration | `services/agent-integration/` | No public HTTP, DB/broker/Gateway wiring (`services/agent-integration/README.md:3-15`) | Local package core only |
| Gateway | `infrastructure/gateway/` | FastAPI app, `/health/live` only (`infrastructure/gateway/goalpulse_gateway/app.py:1-36`) | Stub |
| Frontend | `frontend/` | Next.js scripts; adapter selected by `NEXT_PUBLIC_API_MODE` | Runs in default mock mode |

`[OBSERVED]` Migrations exist at Goal Intelligence `migrations/0001`, Goal Management `alembic/versions/0001`, Discovery `migrations/0001`–`0005`, Data & Evidence `migrations/0001`, Evaluation `migrations/001`–`003`, Notification `migrations/001`–`002`, and Agent Integration `migrations/001`.

Environment-example key names, without values: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, the seven service DB password keys, `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, `GATEWAY_DEV_JWT_SECRET`, `GATEWAY_DEV_JWT_ISSUER`, `GATEWAY_DEV_JWT_AUDIENCE`, `SERVICE_JWT_SECRET`, `SERVICE_JWT_ISSUER`, `SERVICE_JWT_AUDIENCE`, `CONTEXT_DEV_API_KEY`, `NEXT_PUBLIC_API_MODE`, and `NEXT_PUBLIC_GATEWAY_URL`. Goal Intelligence additionally reads `GI_LLM_PROVIDER`, `GI_LLM_MODEL`, `ANTHROPIC_API_KEY`, and database settings in `goal_intelligence/config.py:15-52`.

## 4. **Seven-Service Assessment Matrix**

| Component | Owner | Verdict | Real/mock classification | High-confidence evidence | Top gap |
|---|---|---|---|---|---|
| Goal Intelligence | D2 | PARTIAL | Mixed; real Anthropic adapter, fixture default | `config.py:15-52`, `llm/anthropic.py:1-133` | Not reached by a working Gateway/frontend live path; live call unverified |
| Goal Management | D1 | PARTIAL | Real domain/persistence core, unwired runtime | `api.py:179-308`, `repository.py:27-43`, `worker.py:10-20` | No production app builder; list projection reports hard-coded status |
| Discovery & Monitoring | D3 | PARTIAL | Real Context adapter plus unbound ports | `context_provider.py:106-311`, `workflow.py:30-137` | No activation worker/provider-to-DE production path |
| Data & Evidence | D3 | PARTIAL | Real durable core; trust path blocked | `api.py:64-149`, `ledger.py:217-263` | Cannot assign `checked`; no running publisher/process |
| Goal Evaluation | D4 | PARTIAL | Real deterministic evaluators; mock/injected adapters | `processing.py:1-201`, `consumer.py:20-88` | No concrete clients, HTTP evaluation read, or broker worker |
| Notification | D4 | BLOCKED | Real local durable core, runtime missing | `README.md:3-13`, `consumer.py:39-199` | No broker/client/API process; public contract pending |
| Agent Integration | D4 | BLOCKED | Real local package core with unapproved DTO | `service.py:49-179`, `README.md:3-15` | No worker/API and `seller_orders` contract is unapproved |
| Gateway | D1 | BLOCKED | Stub | `app.py:1-36` | No frozen public routes/proxy/M2M forwarding |
| Frontend | D2 | MOCK_ONLY | Production bundle defaults to in-memory mock | `api/index.ts:7-17`, `api/mock.ts:8-275` | Real Gateway/auth path is unusable; build/e2e failed locally |

### Independent service evidence (A–H)

**Goal Intelligence.** A: `[OBSERVED]` It has a factory entrypoint and `/healthz`, but no Docker readiness. B: it implements `POST /v1/goal-sessions`, `POST/GET /v1/goal-sessions/{id}`, candidate read, and mark-confirmed (`api/app.py:106-139`); internal calls require service JWT and `X-Authenticated-User-Id` (`auth.py:118-143`). C: Pydantic/schema validation and persistent PostgreSQL/SQLite revisions/messages are present; an immutable-revision trigger exists. D: it does not publish/consume RabbitMQ. E: fixture is the default; Anthropic is explicit and failures do not switch providers (`config.py:38-52`). F: tests exist, but `[NOT RUN]` on this host because Python 3.11.5 fails the package's 3.12+ guard. G: secrets remain in env and prompt output is strictly parsed; stored conversation retention/deletion is not implemented. H: **PARTIAL**.

**Goal Management.** A: `[OBSERVED]` real handlers exist but `create_app` needs injected objects and has no standard startup/health. B: it implements the three accepted internal reads, confirmation, goal list/detail, and lifecycle patch (`api.py:200-308`) with user/service JWT and ownership/revision checks. C: GoalSpec validation, per-version immutability, inbox/outbox, and PostgreSQL migration are present. D: an atomic create+activation outbox exists; Pika publisher confirms and a manual-ACK consumer exist (`repository.py:27-43`, `rabbitmq.py:6-12`, `consumer.py:20-87`). E: real core, unwired. F: offline tests passed within the 717-test suite. G: authentication and ownership checks are explicit; the dashboard list hard-codes `not_found`/`None` instead of using its evaluation projection (`repository.py:64-70,86-121`). H: **PARTIAL**.

**Discovery & Monitoring.** A: no production startup/health or Dockerfile; the live probe is separate. B: only source and subscription internal GET routes are exposed (`api.py:20-46`); no webhook HTTP route. C: migrations cover durable inbox/jobs/sources/subscriptions/monitor state; `GoalReader`, `EvidenceSink`, and `SecretStore` remain protocols. D: activation job/dedup and monitoring reservation logic exist, but no concrete RabbitMQ consumer or retry/DLQ process. E: a real Context.dev adapter exists; only `probe.py` constructs it in non-test source. F: offline tests passed; PostgreSQL integration tests skipped without DSN. G: URL checks, HMAC webhook verification, dedup, and redacted client repr exist; provider redirect/DNS enforcement and a real secret store remain unverified. H: **PARTIAL**, blocked for E2E.

**Data & Evidence.** A: API factory exists with injected dependencies; no production startup/health. B: implements accepted extraction POST and observation GET/list routes (`api.py:72-149`). C: strict extraction input, provenance-aware immutable observations, failure ledger, outbox, and owned DB exist. D: atomic observation+outbox and a Rabbit channel publisher adapter exist; no running worker/process. E: real durable core; provider submissions are external inputs. F: offline tests passed; PostgreSQL integration tests skipped without DSN. G: missing timestamp, price, name, or unit produces a failure record rather than zero/fabricated data (`api.py:52-99`); however the reliability mapping prevents `checked`. H: **PARTIAL**.

**Goal Evaluation.** A: no app startup/health; processing explicitly uses injected/mock adapters. B: the accepted internal evaluation GET is not implemented. C: deterministic product/procurement evaluators, persistence, inbox/outbox, and migrations exist. D: durable event processing and dedup exist, but no concrete RabbitMQ worker/client. E: real evaluators, incomplete runtime adapters. F: offline tests passed. G: stale goal version, evidence freshness, checked reliability, stock, shipping, tax, condition, and unknown gates are conservative (`evaluators/product.py:124-192`). H: **PARTIAL**.

**Notification.** A/B: no runnable HTTP/broker process or public route. C: PostgreSQL notification/dedup state and owner-filtered local reads exist. D: event/idempotency and retry decisions exist but no broker adapter is bound. E: real core only. F: offline tests passed. G: checked/current evidence and owner-specific reads are enforced in core; end-to-end user isolation is unverified. H: **BLOCKED**.

**Agent Integration.** A/B: no runnable worker/HTTP route. C: package builder, durable store/migration, expiry, evidence fields, approval requirement, and `auto_execute=false` exist. D: replay-safe local consumer logic exists, without broker binding. E: real core using a service-local proposed schema. F: offline tests passed. G: package reads filter owner and unsafe URLs are tested; no credential or purchasing action is implemented. H: **BLOCKED** pending runtime and ICP-02/schema approval.

**Gateway.** A: FastAPI app with liveness only. B: none of the seven frozen public paths are proxied. C/D: no owned business DB or RabbitMQ role. E: stub. F: local Gateway tests passed in the offline suite. G: local HS256 verification exists (`auth.py:44-103`), but there is no forwarding path, CORS/body/rate policy, or demonstrated M2M exchange. H: **BLOCKED**.

**Frontend.** A: Next.js app with no container/health definition. B: the Gateway client implements the seven frozen public calls (`api/gateway.ts:24-87`), while notification/package calls are mock-only. C: default mock state is held in Maps and is lost on restart. D: no direct broker access, correctly. E: mock is the fail-open default. F: typecheck/lint/unit passed; build and Playwright failed locally. G: gateway mode accepts a pasted token in `sessionStorage` (`auth.ts:1-46`); this is not a production identity flow. H: **MOCK_ONLY** as shipped.

## 5. **Mock and Fixture Exposure Register**

| Component | File:line | Selection/default | Scope | User-visible effect | Replacement needed | Owner | Priority |
|---|---|---|---|---|---|---|---|
| Frontend API adapter | `frontend/src/lib/api/index.ts:7-17` | `NEXT_PUBLIC_API_MODE`; anything except exact `gateway` becomes mock; default yes | Production bundle | All displayed goals/conversations can be synthetic | Fail closed in demo/prod and require reachable Gateway | D2 | P0 |
| Frontend in-memory backend | `frontend/src/lib/api/mock.ts:8-275` | Selected by mock adapter; default yes | Production bundle | Seeded goals/offers, scripted state, no restart persistence | Real Gateway mode and explicit development-only mock banner/build gate | D2 | P0 |
| Scripted clarifications | `frontend/src/lib/api/mock-scripts.ts:1-9` | Mock mode | Production bundle | Questions appear dynamic but are fixed test/demo text | Real GI session calls | D2 | P0 |
| Proposed UI data | `frontend/src/lib/api/proposed.ts:4-11` | Available only in mock mode | Production bundle | Notifications/action packages are unavailable in gateway mode | Approve ICP-02/public extension, then Gateway client | D1/D2/D4 | P0 |
| Frontend Playwright server | `frontend/playwright.config.ts:33-39` | Forces `NEXT_PUBLIC_API_MODE=mock` | Test | E2E covers only fake data | Add a separate full-stack profile; retain mock test for speed | D2/D1 | P1 |
| Frontend CI build | `.github/workflows/frontend.yml:33-58` | Build explicitly mock; Playwright named mock mode | CI | CI cannot detect broken live adapter/backend integration | Add no-silent-mock and live-stack contract job | D1/D2 | P0 |
| GI fixture extractor | `services/goal-intelligence/goal_intelligence/config.py:15-52`; `llm/fixture.py:1-122` | `GI_LLM_PROVIDER=fixture`; default yes | Production-capable runtime | Rule-based questions/drafts, not a live model | Explicit demo config `anthropic`, secrets, smoke test, startup mode disclosure | D2 | P0 |
| GI fixture fallback behavior | `services/goal-intelligence/goal_intelligence/service.py:122-146` | Extractor error returns an empty patch/retry notice; does not switch adapter | Production | Failure is visible but may still append a revision later | Align revision semantics/docs and test provider outage | D2 | P1 |
| DM injected ports | `services/discovery-monitoring/goalpulse_discovery/workflow.py:30-39` | No production binding | Production gap | Activation cannot reach GoalSpec or submit extraction | Concrete authenticated GM/DE clients and worker builder | D3 | P0 |
| DM live probe-only binding | `services/discovery-monitoring/goalpulse_discovery/probe.py:33-60` | Separate command | Developer tool | A successful probe would not prove user workflow is live | Instantiate adapter in main worker path | D3 | P0 |
| DE fixture ingestion values | `services/data-evidence/goalpulse_evidence/api.py:31-42`; `ledger.py:91-93` | Request body may select `provider=fixture` and `extraction_method=fixture`; no environment gate | Production internal route | A service caller could persist synthetic evidence in a non-test runtime | Reject fixture provenance in demo/prod or enforce an explicit development profile | D3 | P0 |
| DE reliability suggestion | `services/data-evidence/goalpulse_evidence/api.py:28,119-125` | `checked` accepted but downgraded | Production | No live product can satisfy checked-evidence gate | Approve/implement ICP-09 corroboration policy | D3 | P0 |
| GE orchestration adapters | `services/goal-evaluation/goal_evaluation/processing.py:1-69` | Protocols/in-memory test adapters | Production gap | Events cannot fetch live goal/observations | Concrete service-auth HTTP clients and worker | D4 | P0 |
| Procurement normalization flag | `services/goal-evaluation/goal_evaluation/evaluators/procurement.py:42-63` | Defaults false | Production | Real café basket remains unknown | Approved normalization/quality inputs and integration | D3/D4 | P1 |
| NS upstream adapters | `services/notification/notification/consumer.py:39-49` | Protocols/test doubles | Production gap | No live notification is created | Concrete readers and Rabbit worker | D4 | P0 |
| AI upstream adapters/DTO | `services/agent-integration/agent_integration/service.py:49-179` | Protocols/test doubles; local schema | Production gap | No live package; UI shape disagrees | Approve shape, then worker/read API | D4 | P1 |
| Gateway shell | `infrastructure/gateway/goalpulse_gateway/app.py:1-36` | No mode switch; only liveness is implemented | Production stub | Real frontend requests have no destination | Implement frozen-route proxy and service-claim forwarding | D1 | P0 |

`[OBSERVED]` Hard-coded `shop.example`, sample GPUs, café goals, offers, and a demo user appear in frontend mock/test data. They are appropriately isolated by the adapter at runtime, but because mock is the default they are the current user experience. `[OBSERVED]` No real-provider failure silently changes `GI_LLM_PROVIDER` to fixture; the selected extractor returns a controlled failure instead.

## 6. **End-to-End Trace A–E**

### Scenario A — Goal drafting and confirmation

| Hop | State | Evidence and finding |
|---|---|---|
| Browser accepts natural language | MOCK by default / REAL UI | `frontend/src/lib/api/index.ts:7-17`; the UI calls an adapter, defaulting to in-memory mock |
| Dynamic LLM question | MOCK default; REAL adapter UNVERIFIED | Fixture is default (`config.py:18,38-46`); Anthropic adapter exists (`llm/anthropic.py:1-133`) but was not called |
| Revision and stale 409 | REAL core, MISSING live route chain | GI stores revisions and GM preserves expected-revision conflict; Gateway exposes no route |
| Strict summary and confirm | REAL UI/core, MISSING integration | GI/GM validators and confirm handler exist (`goal_management/api.py:226-287`); browser cannot reach them through Gateway |
| Durable goal + activation + dashboard restart | PARTIAL | GM atomically creates goal/version/outbox (`repository.py:27-43`), but no executable stack; list projection hard-codes `not_found` (`repository.py:64-70`) |

### Scenario B — Live product data

| Hop | State | Evidence and finding |
|---|---|---|
| Activation triggers discovery | MISSING | GM can publish, DM has durable job logic, but no bound activation RabbitMQ worker |
| Initial Context search/scrape | REAL adapter, MISSING main path, NOT RUN | Fixed Context host and search/scrape/monitor methods exist (`context_provider.py:106-311`); only probe constructs adapter |
| Business-success/freshness fields | PARTIAL | Adapter and ledger preserve provider/request/cache/observed metadata; a concrete mapping into `ExtractionSubmission` is missing |
| Immutable Observation/event or failure | REAL core, MISSING runtime | Missing required facts go to failure ledger and no zero price is fabricated (`api.py:52-125`); publisher has no running process |
| Evidence triggers evaluation | MISSING runtime | Routing is declared, but GE has no concrete broker worker/read clients |
| Unknown blocks satisfaction | REAL unit-tested core | Product evaluator requires checked, fresh, available, matching, shipping/tax-known evidence (`product.py:124-192`) |

### Scenario C — Notification and agent handoff

| Hop | State | Evidence and finding |
|---|---|---|
| Canonical opportunity event once | REAL core, MISSING runtime | GE fingerprint/dedup/outbox logic exists; no worker completes the path |
| Owner-specific notification on replay | REAL core, MISSING runtime | NS durable dedup and owner filtering exist; no broker or API binding |
| Read-only package with expiry/approval | REAL core, MISSING runtime | AI sets approval and non-execution semantics, but has no broker/read API and uses unapproved DTO |
| UI shows owner-only/non-purchase state | MOCK only | Mock UI is owner-scoped by demo data and does not claim purchase; real auth/Gateway path is absent |

### Scenario D — Pause, replay, and failure

| Hop | State | Evidence and finding |
|---|---|---|
| Pause/new version | REAL GM core, MISSING E2E | Lifecycle route and immutable versions exist |
| Stale webhook/event | REAL local guards | D3/D4 cores compare active goal version and deduplicate event IDs |
| Suppress false downstream output | REAL unit behavior, UNVERIFIED integration | Evaluator/NS/AI tests cover stale/duplicate cases, but no broker-stack test exists |
| Outage is observable/recoverable | PARTIAL | Inboxes, outboxes, retries, DLQs, and failure ledgers exist; no running workers, metrics, tracing, or operator replay procedure is demonstrated |

### Scenario E — Recurring café procurement

| Hop | State | Evidence and finding |
|---|---|---|
| Parse multiple items/units | REAL fixture/core; live LLM UNVERIFIED | GI schema/parser supports recurring procurement; external model was not called |
| Obtain real item observations | MISSING | No live main path was run or wired |
| Basket optimization | REAL deterministic core, BLOCKED inputs | Evaluator includes quantity/delivery/shipping-once/tax and unknown gates, but normalized-cost policy defaults false (`procurement.py:42-63`) |
| Truthful dashboard coverage | PARTIAL/MISSING | UI avoids claiming purchase/completion, but live progress/source coverage is not implemented; GM list projection is static |

## 7. **LLM Integration Deep Dive**

`[OBSERVED]` Goal Intelligence defines an injected extractor interface and two implementations. The deterministic fixture parser is in `llm/fixture.py:1-122`. The optional Anthropic Messages adapter uses a fixed HTTPS endpoint, configurable model, bounded response tokens, temperature zero, transport timeout, strict JSON parsing, and error normalization (`llm/anthropic.py:1-133`). `ResilientExtractor` bounds input, timeout, retry count, and exponential backoff (`llm/base.py:72-131`). Parsed output is validated before domain merge.

`[OBSERVED]` Runtime selection is explicit in `config.py:38-52`: `fixture` is the default, `anthropic` requires its API key, and an unknown provider raises. There is no OpenAI adapter. A real-provider error is converted into an empty patch plus a retry notice; it does not silently invoke the fixture (`service.py:122-146`). `[OBSERVED]` The implementation appears to append a new draft revision after this controlled failure, which conflicts with the module-level claim that a failed extractor leaves the revision unchanged; this should be resolved in R2-D2-01.

`[INFERRED]` Privacy controls are incomplete for a real demo. Prompts contain user goal text and messages are persisted, but no retention/deletion policy, consent disclosure, or field-level redaction path was observed. The key is env-based and not logged by the adapter. Rate limits are represented only as provider errors/retries, not an application-level quota or circuit breaker.

`[UNVERIFIED]` No real Anthropic response, account/model entitlement, latency, structured-output conformance, rate-limit behavior, or billing behavior was tested. The browser cannot currently reach this adapter through the Gateway. A later controlled smoke test should set `GI_LLM_PROVIDER=anthropic`, a permitted `GI_LLM_MODEL`, and a secret supplied by the environment, then send one non-sensitive session request. It must assert: provider mode is disclosed; a dynamic question is returned; malformed/timeout responses do not activate a goal or fall back to fixture; and no secret/prompt is logged. This command is intentionally not prescribed with a token or live endpoint payload in the report.

## 8. **Context.dev and Evidence Deep Dive**

`[OBSERVED]` `ContextProvider` is a real standard-library HTTP adapter constrained to `api.context.dev`. It implements search, scrape, monitor creation, and monitor-run retrieval (`context_provider.py:106-311`). It validates public HTTPS targets, rejects private/link-local/metadata-style destinations, redacts its key in `repr`, preserves request IDs when available, and separates request time from provider/cache observation time (`context_provider.py:60-83,168-189`).

`[OBSERVED]` The main discovery workflow receives `GoalReader` and `EvidenceSink` protocols and can search/scrape/submit candidates (`workflow.py:30-137`), but no production binding was found. The only non-test construction of `ContextProvider` is the opt-in probe (`probe.py:33-60`). The older `scripts/context_probe.py` depends on an external SDK and can print raw markdown; it should not be used for a production-readiness smoke because it is less redacted.

`[OBSERVED]` Monitoring reservation, source sharing, quotas, baseline state, signed webhook validation, dedup, and durable job creation exist as cores (`monitoring.py:77-144`, `webhooks.py:31-139`). The webhook module explicitly has no HTTP route; `SecretStore` is a protocol; and no scheduler/worker binds these pieces. Provider-side redirects and DNS rebinding behavior remain `[UNVERIFIED]`.

`[OBSERVED]` Data & Evidence refuses observations without a reliable observed timestamp and records missing identity/price/unit facts in a failure ledger (`ledger.py:114-124`, `api.py:52-99`). It does not fabricate `unit_price_eur_cents=0`. The material blocker is trust: input `checked` is downgraded to `unverified`, while downstream product evaluation requires checked (`api.py:119-125`, `product.py:160-173`). A live scraped price therefore cannot become a verified offer under current code. This is conservative and truthful, but ICP-09 needs an approved corroboration/checking rule before Phase 2 can be complete.

Controlled later smoke command, not executed:

```bash
PYTHONPATH=services/discovery-monitoring python services/discovery-monitoring/probe_context.py https://<approved-public-product-url>
```

Run only with an approved non-production key and URL. Assert redacted logs, provider business status, request ID, provider/cache timestamp, product identity, EUR price, availability, Finnish shipping, shipping/tax status, and safe failure when required facts are absent. Probe success alone must not be accepted as E2E success.

## 9. **Contracts, Events, and DB Consistency**

`[OBSERVED]` The frozen sources remain `contracts/schemas/*.v1.schema.json`, `contracts/openapi/gateway.v1.yaml`, and `contracts/event-routing.yaml`. Contract validation passed for 13 examples, five schemas, four event routes, and seven public paths. The four declared routes are `goal.activated.v1`, `evidence.updated.v1`, `goal.evaluated.v1`, and `goal.opportunity_verified.v1` (`contracts/event-routing.yaml:1-23`). No implementation was found publishing an invented public event name.

`[OBSERVED]` ADR-0002 accepts only ICP-01 (the historically named internal OpenAPI) and ICP-08 (service JWT), and `docs/implementation/DECISIONS.md:7-9` records ICP-01, ICP-08, and the accepted handling for ICP-13. The remaining status is:

| Decision | Status/evidence | Implementation consequence |
|---|---|---|
| ICP-01 internal API | ACCEPTED | GI, GM, DM, and DE routes substantially match; accepted GE evaluation GET and AI package GET are missing |
| ICP-02 public notifications/packages | PENDING | Frontend implements only mock reads; proposed OpenAPI is not authoritative |
| ICP-03 reconciliation trigger | PENDING | Local reconciliation cores exist; no scheduler and no invented event |
| ICP-04 Finland eligibility | PENDING | Conservative local policy exists; no approved shared rule |
| ICP-05 extraction failure semantics | PENDING | Failure ledger avoids fabricated observations |
| ICP-06 staleness semantics | PENDING | Local flags/semantics exist; no approved live normalization |
| ICP-07 goal edit endpoint | DEFERRED/PENDING | No edit endpoint is implemented |
| ICP-08 service authentication | ACCEPTED | HS256 implementations exist; Gateway forwarding and E2E claims are not wired |
| ICP-09 corroboration/reliability | PENDING, P0 | Current path can never promote submitted evidence to `checked` |
| ICP-10 notification/package fingerprint | PENDING | D4 cores implement local fingerprints; no accepted shared shape |
| ICP-11 quotas/baseline | PENDING | D3 core exists; no operational scheduler |
| ICP-12 webhook/provider security | PENDING | HMAC/SSRF core exists; no HTTP ingress/secret backend |
| ICP-13 missing condition/attributes | ACCEPTED handling | Missing facts remain `unknown` |

Specific drift:

- `[OBSERVED]` `contracts/openapi/internal.proposed.v1.yaml:328-349` and the public extension proposal use flat `seller_offers`. Agent Integration implements service-local `seller_orders` (`agent_integration/schemas/action-package.v1.schema.json:43-161`). This is not an approved interface and must not be exposed until ICP-02 is resolved.
- `[OBSERVED]` `contracts/openapi/gateway.v1.yaml` describes the initial public contract as not implemented; the frontend client matches its seven paths, but the Gateway does not implement them.
- `[OBSERVED]` Public monetary fields and domain logic use integer EUR cents. No binary floating-point financial decision path was observed in the audited core.
- `[OBSERVED]` Travel remains unsupported by v1 schemas/domain validators; no truthful implementation claims universal goal execution.
- `[OBSERVED]` All seven services have owned migrations. No cross-service SQL import/join was observed; runtime boundaries use protocols or HTTP proposals.

## 10. **Security and Operational Readiness**

### Positive controls observed

- Goal and draft handlers validate user/service JWT claims, derive user ownership server-side, check ownership, and preserve stale-revision conflict behavior.
- Service JWT verification is implemented locally; keys are configured by environment names rather than embedded production secrets.
- Product evaluation treats missing, stale, unchecked, wrong-version, non-new, unavailable, or shipping/tax-unknown evidence conservatively.
- Context URL screening, webhook HMAC verification, webhook dedup, immutable evidence provenance, and unsafe package URL tests exist.
- Agent packages require approval, set `auto_execute=false`, expire, and contain no purchase action.
- Rabbit definitions specify durable topic exchange/queues, quorum queues, DLXs/DLQs, and exact routing bindings.

### Blocking operational/security gaps

- `[OBSERVED]` The Gateway does not proxy, mint/forward service identity, apply route ownership, configure CORS/body limits/rate limits, or expose readiness. Direct service assumptions are safe only if services are network-isolated; no deploy topology proves that.
- `[OBSERVED]` Frontend gateway mode uses a manually pasted token stored in `sessionStorage`, not a real IdP/session flow (`frontend/src/lib/auth.ts:1-46`). The default hard-coded demo user belongs only to mock mode and must never become server authentication.
- `[OBSERVED]` There is no concrete webhook ingress/secret backend or demonstrated provider redirect/DNS enforcement. HMAC code alone does not prove an authenticated endpoint.
- `[OBSERVED]` No application Docker health/readiness probes, metrics endpoint, distributed trace propagation, centralized structured logging policy, operator replay command, or alerting configuration was found.
- `[INFERRED]` At-least-once safety is strong in individual cores but unverified across broker reconnects, service restarts, delayed stale events, and partial HTTP outages because no cross-service integration test runs them.
- `[UNVERIFIED]` Deployment network policy, TLS termination, secret rotation, database roles/backups, dependency vulnerability state, and production logs were outside local repository evidence.

No secrets were printed. The local `.env` file's presence was not treated as proof that any external system is configured.

## 11. **Tests Executed and Not Executed**

| Command | Exit | Result |
|---|---:|---|
| `python scripts/validate_contracts.py` with provider/test DSN env vars unset | 0 | 13 examples, 5 schemas, 4 event routes, and 7 OpenAPI paths validated |
| `python -m pytest -q -rs` with external/provider env vars unset | 2 | Collection stopped: host Python 3.11.5, Goal Intelligence requires Python 3.12+ |
| `python -m pytest -q -rs tests infrastructure/tests infrastructure/gateway/tests services/goal-management/tests services/discovery-monitoring/tests services/data-evidence/tests services/goal-evaluation/tests services/notification/tests services/agent-integration/tests` with external/provider env vars unset | 0 | 717 passed, 7 skipped; 3 DM PostgreSQL tests skipped without `GOALPULSE_DM_TEST_DSN`, 4 DE PostgreSQL tests skipped without `GOALPULSE_DE_TEST_DSN` |
| `npm test` in `frontend/` | 0 | 5 test files passed |
| `npm run typecheck` in `frontend/` | 0 | Passed |
| `npm run lint` in `frontend/` | 0 | Passed |
| `npm run build` in `frontend/` | 1 | Next compilation completed, then failed: `Could not parse output from TypeScript's --showConfig` |
| `npm run e2e` in `frontend/` | 1 | Playwright webServer process failed to start, exit 1 |
| `docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet` | 0 | Compose syntax/config accepted without printing expanded values |

Not executed:

- `[NOT RUN]` Goal Intelligence tests: no Python 3.12 interpreter was available locally. CI configuration targets 3.12, but no CI result was queried.
- `[NOT RUN]` Seven skipped PostgreSQL integration tests: DSNs were intentionally unset; running them would mutate a database.
- `[NOT RUN]` Containers, migrations, RabbitMQ workers, and replay/outage tests: user permission would be required for startup/mutation and the stack lacks application services.
- `[NOT RUN]` Anthropic, Context.dev, email, payment, booking, or any other external API: prohibited by audit scope.
- `[NOT RUN]` Live JWT/IdP, webhook delivery, production logs, and deployed health checks: no approved environment was supplied.

CI was inspected as configuration only. `[UNVERIFIED]` Current CI run status is unknown. The root workflow installs only Gateway, GM, and GI service dependencies before running the entire root test suite (`.github/workflows/contracts.yml:14-21`); dedicated D3/D4 PostgreSQL/broker coverage is absent.

## 12. **Prioritized Phase 2 Backlog**

### R2-D1-01 — Executable Gateway and local application stack

- **Priority/owner/complexity:** P0, D1, L.
- **Paths:** `infrastructure/gateway/`, `infrastructure/docker-compose.yml`, service runtime packaging; shared files require D1 coordination.
- **Finding:** Gateway is liveness-only and Compose has no app containers.
- **Dependencies:** ICP-01/08 already accepted; settle external identity configuration.
- **Minimal work:** implement the seven frozen proxies with user-to-service claim forwarding, error/status preservation, readiness, bounded bodies/timeouts; add reproducible service commands/health checks to a demo profile.
- **Acceptance:** from a clean Python 3.12/container environment, one command starts dependencies/apps; authenticated contract tests exercise all seven frozen routes; unauthenticated/cross-owner requests fail; no service is directly public.
- **Handoff:** runtime URLs/service-JWT expectations to D2–D4.
- **Contract/ADR:** no frozen contract change; deployment/security ADR update required.

### R2-D1-02 — Goal Management production builder and truthful projections

- **Priority/owner/complexity:** P0, D1, M.
- **Paths:** `services/goal-management/`.
- **Finding:** real core has no app builder; goal list hard-codes opportunity state.
- **Dependencies:** R2-D1-01; existing accepted internal reads.
- **Minimal work:** bind repository, candidate client, outbox publisher/consumer, health/readiness; read the durable evaluation projection in list/detail.
- **Acceptance:** restart test confirms a goal persists and activation publishes once; replay is idempotent; list reflects a stored current-version evaluation and ignores stale versions.
- **Handoff:** stable activation/event and public goal reads to D3/D2.
- **Contract/ADR:** no, unless response semantics exceed frozen fields.

### R2-D3-01 — Bind live discovery workflow

- **Priority/owner/complexity:** P0, D3, L.
- **Paths:** `services/discovery-monitoring/`, `services/data-evidence/` client boundary.
- **Finding:** real Context adapter exists only in a probe; workflow ports are unbound.
- **Dependencies:** R2-D1-01/02, approved provider account; ICP-04/06/11/12 decisions as applicable.
- **Minimal work:** add authenticated GM reader and DE extraction client, activation Rabbit worker, provider mapper, bounded retry, scheduler, and readiness; preserve baseline vs change behavior; reject `fixture` provenance in demo/prod.
- **Acceptance:** recorded-provider integration fixture proves activation causes one initial search/scrape and a valid extraction request; duplicate activation and restart do not duplicate jobs; controlled live smoke records request/freshness metadata without raw page logs; demo/prod cannot persist fixture provenance.
- **Handoff:** stable extraction/failure records to D3 evidence and D4.
- **Contract/ADR:** likely policy ADR; no v1 event addition.

### R2-D3-02 — Approve and implement checked-evidence policy

- **Priority/owner/complexity:** P0, D3 with D1/D4 approval, M.
- **Paths:** `docs/implementation/DECISIONS.md`, new ADR if approved, `services/data-evidence/`, related fixtures/tests; protected docs/contracts only after approval.
- **Finding:** all non-disputed HTTP extractions become unverified; downstream requires checked.
- **Dependencies:** explicit ICP-09 decision and threat model.
- **Minimal work:** define deterministic corroboration/checking authority, provenance, staleness, mismatch and dispute rules; implement without trusting provider-supplied reliability.
- **Acceptance:** fixtures show a corroborated option becomes checked, a single/ambiguous/stale source stays unverified, and conflicts become disputed; provenance and EUR-cent invariants validate.
- **Handoff:** documented reliability guarantee to Evaluation/NS/AI.
- **Contract/ADR:** **yes, ADR/policy approval required**; versioned contract change only if fields must change.

### R2-D4-01 — Evaluation runtime and accepted read API

- **Priority/owner/complexity:** P0, D4, L.
- **Paths:** `services/goal-evaluation/`.
- **Finding:** deterministic cores are not bound to real readers, HTTP, or RabbitMQ.
- **Dependencies:** R2-D1-01/02 and R2-D3-02.
- **Minimal work:** add authenticated GM/DE clients, accepted evaluation GET, broker worker with manual ACK/retry/DLQ, process builder, readiness, and migration/startup documentation.
- **Acceptance:** broker integration test evaluates the current version, emits once, treats missing facts as unknown, survives duplicate/restart, and rejects stale version evidence.
- **Handoff:** stable evaluation read/event/fingerprint to D1/NS/AI.
- **Contract/ADR:** no for ICP-01; ICP-10 approval needed before shared fingerprint semantics are declared final.

### R2-D2-01 — Real LLM demo mode and no-silent-fallback gate

- **Priority/owner/complexity:** P0, D2, M.
- **Paths:** `services/goal-intelligence/`, its tests/config/docs.
- **Finding:** real adapter exists but fixture is default and live behavior is unverified; failure revision semantics appear inconsistent.
- **Dependencies:** approved provider/model/privacy policy, R2-D1-01.
- **Minimal work:** explicit environment profiles, startup disclosure, fail-closed demo/prod configuration, structured-output fixtures, failure/retry/rate-limit tests, retention decision.
- **Acceptance:** a test fails startup if demo/prod selects fixture; controlled smoke creates a dynamic valid draft; timeout/malformed output cannot confirm or switch to fixture; revision behavior matches docs.
- **Handoff:** stable session behavior to frontend/GM.
- **Contract/ADR:** privacy/operations decision; no v1 schema change expected.

### R2-D2-02 — Real frontend/Gateway authentication path

- **Priority/owner/complexity:** P0, D2 with D1, M.
- **Paths:** `frontend/`, Gateway auth integration.
- **Finding:** default mock adapter and token-paste session; proposed screens are mock-only.
- **Dependencies:** R2-D1-01, identity decision, ICP-02 for notification/package screens.
- **Minimal work:** explicit development mock profile, fail-closed demo/prod build, real session mechanism, error/loading/retry states, remove demo identity from real path.
- **Acceptance:** production build contains `gateway` mode, refuses missing Gateway/auth config, passes cross-owner UI/API tests, and visibly identifies mock builds.
- **Handoff:** browser acceptance harness for R3.
- **Contract/ADR:** identity ADR; ICP-02 approval for new reads.

### R2-D2-03 — Restore frontend production build and E2E start

- **Priority/owner/complexity:** P0, D2, S/M.
- **Paths:** `frontend/package.json`, Next/TypeScript config, Playwright config/tests.
- **Finding:** build and Playwright exited 1 locally after other checks passed.
- **Dependencies:** none; then R2-D2-02.
- **Minimal work:** reproduce under CI Node 22, fix `--showConfig` parsing/start command without suppressing errors, keep mock E2E as a fast lane.
- **Acceptance:** `npm ci && npm run typecheck && npm run lint && npm test && npm run build && npm run e2e` exits 0 on the documented Node version.
- **Handoff:** reliable frontend gate.
- **Contract/ADR:** no.

### R2-D4-02 — Notification runtime and owner read API

- **Priority/owner/complexity:** P1 (becomes P0 for full A–C demo), D4, M.
- **Paths:** `services/notification/`, Gateway/frontend after approval.
- **Finding:** durable core has no broker/client/API binding.
- **Dependencies:** R2-D4-01, ICP-02/10 decisions, R2-D1-01.
- **Minimal work:** bind readers/consumer, owner-filtered read API, retry/DLQ visibility and readiness.
- **Acceptance:** replay produces one notification, stale/unchecked opportunity produces none, another owner receives 404/empty result, restart preserves state.
- **Handoff:** accepted read DTO to Gateway/frontend.
- **Contract/ADR:** **yes**, ICP-02 and ICP-10 approval/versioned extension.

### R2-D4-03 — Agent Integration runtime and package contract alignment

- **Priority/owner/complexity:** P1, D4, M.
- **Paths:** `services/agent-integration/`, proposed OpenAPI/contracts only after approval.
- **Finding:** local `seller_orders` differs from proposed `seller_offers`; no worker/API.
- **Dependencies:** R2-D4-01, ICP-02/10 decision.
- **Minimal work:** approve one DTO, bind readers/consumer/read API, preserve expiry/revalidation/prepare-only behavior.
- **Acceptance:** replay creates one current owner package; expired/stale/unsafe/unchecked evidence blocks use; `auto_execute` remains false; schema validates at Gateway boundary.
- **Handoff:** approved DTO to D1/D2.
- **Contract/ADR:** **yes**, approved versioned public/internal extension and ADR.

### R2-D4-04 — Recurring procurement live normalization

- **Priority/owner/complexity:** P1, D4 with D3, M/L.
- **Paths:** `services/goal-evaluation/`, D3 extraction/normalization.
- **Finding:** optimizer exists, but normalized-cost confirmation defaults false and real multi-item evidence is absent.
- **Dependencies:** R2-D3-01/02, R2-D4-01, ICP-04/06.
- **Minimal work:** define unit conversions/quality equivalence, seller shipping-once and tax semantics, and source-coverage output without a fake percentage.
- **Acceptance:** deterministic kg/L/pcs fixture plus controlled evidence proves correct totals; any unknown item/shipping/tax/quality blocks verification.
- **Handoff:** truthful procurement evaluation to UI.
- **Contract/ADR:** policy ADR; version change only if output shape changes.

### R2-D1-03 — Cross-service replay, outage, and observability gate

- **Priority/owner/complexity:** P0 integration task, D1 coordinating D2–D4, L.
- **Paths:** `infrastructure/`, integration tests, CI workflows; protected paths require approval.
- **Finding:** unit-level durability exists but no full-stack proof.
- **Dependencies:** all P0 runtimes above.
- **Minimal work:** offline recorded-provider stack test; broker pause/restart, duplicate/stale event, provider timeout, service 5xx, and DB restart scenarios; correlation-safe logs/metrics; operator replay documentation.
- **Acceptance:** scenarios A–D pass after restart with exactly-once business outcomes under at-least-once delivery; no fake success; CI captures redacted diagnostics.
- **Handoff:** Gate R3 evidence to all owners.
- **Contract/ADR:** operations ADR/CI approval; no new event names.

## 13. **Recommended Parallel Work Plan and Integration Gates**

| Gate | Required result | Parallel work |
|---|---|---|
| **R0 — Decisions and reproducibility** | Approve/reject ICP-02, 04, 06, 09, 10–12; reproduce frontend build; document Python 3.12/Node 22 baseline | D1 identity/deployment ADR; D2 build and LLM failure tests; D3 reliability proposal; D4 DTO/fingerprint proposal |
| **R1 — Executable skeleton** | Gateway proxies frozen routes; all seven service processes start with readiness; DB migrations and broker bindings are automated in an isolated test stack | D1 Gateway/GM; D2 GI/frontend; D3 DM/DE bindings; D4 GE runtime |
| **R2 — Recorded-provider real data path** | Browser→GI→GM→activation→recorded Context response→DE→GE works with persistent restart and unknown gates; no external call required in CI | Teams work in ownership boundaries against accepted interfaces |
| **R3 — Replay/security/full A–D** | Duplicate/stale/outage tests pass; owner isolation and M2M are verified; NS/AI approved reads work; no-silent-mock gate passes | D4 NS/AI after ICP approval; D1 integration/observability; D2 UI acceptance |
| **R4 — Controlled live smoke/demo sign-off** | One approved non-sensitive Anthropic call and Context.dev probe/main-path run, followed by truthful UI evidence; costs/keys monitored; no purchase action | Sequential sign-off by D2, D3, D4, then D1 release owner |

Safe parallelization depends on freezing test fixtures and client interfaces at each gate. Do not let D2 consume `seller_orders`, or D4 expose it, before ICP-02 is accepted. Do not let D4 relax `checked` locally to unblock the demo; D3/D1 must approve the reliability authority first.

## 14. **Open Decisions for the Four Developers**

1. **D1:** Yes/no — will demo/prod startup fail if the Gateway cannot validate user JWTs and mint/forward accepted ICP-08 service claims, rather than exposing services directly?
2. **D1 + all affected owners:** Yes/no — approve an ADR and versioned public extension for notification and action-package reads (ICP-02), rather than treating proposed YAML as a contract?
3. **D3 + D4 + D1:** Yes/no — approve a deterministic evidence corroboration authority capable of assigning `checked` (ICP-09), with conflicts becoming `disputed` and single-source ambiguity remaining unverified?
4. **D3:** Yes/no — require provider timestamps/request IDs and an explicit cache-age ceiling before an observation may be considered current (ICP-06)?
5. **D3 + D1:** Yes/no — expose a signed webhook HTTP ingress only after a managed secret store, redirect/DNS policy, replay window, and rotation procedure are defined (ICP-12)?
6. **D4 + D1 + D2:** Yes/no — select grouped `seller_orders` as a new approved DTO, replacing the flat `seller_offers` illustration, with a compatibility/versioning plan?
7. **D4 + D3:** Yes/no — define normalized unit/quality equivalence as required input for recurring procurement rather than inferring it from product text?
8. **D2 + D1:** Yes/no — make fixture/mock selection a startup/build error in demo/prod while retaining explicit local and CI mock profiles?
9. **D2:** Yes/no — adopt a documented LLM data-retention/redaction policy before sending real user goal text to a provider?
10. **All owners:** Yes/no — require Gate R3 recorded-provider replay/outage evidence before any Gate R4 live-provider demo?

## 15. **Appendix: Evidence Index**

| Topic | Primary evidence |
|---|---|
| Repository policy and architecture | `AGENTS.md`; `docs/architecture.md`; `docs/data-model.md`; `docs/adr/0001-microservices-contracts.md` |
| Accepted decisions | `docs/adr/0002-internal-api-and-service-auth.md:3-70`; `docs/implementation/DECISIONS.md:7-9`; `docs/implementation/12-contract-gaps-and-decisions.md:7-19` |
| Frozen public API/events | `contracts/openapi/gateway.v1.yaml:1-156`; `contracts/event-routing.yaml:1-23`; `contracts/schemas/` |
| Proposed interfaces | `contracts/openapi/internal.proposed.v1.yaml:127-350`; `contracts/openapi/gateway.extensions.proposed.v1.yaml:1-120` |
| Compose/Rabbit topology | `infrastructure/docker-compose.yml:1-60`; `infrastructure/rabbitmq/definitions.json:4-29` |
| Gateway | `infrastructure/gateway/goalpulse_gateway/app.py:1-36`; `auth.py:44-103` |
| GI routes/provider selection | `services/goal-intelligence/goal_intelligence/api/app.py:106-139`; `config.py:15-52`; `llm/base.py:72-131`; `llm/anthropic.py:1-133` |
| GM API/durability | `services/goal-management/src/goal_management/api.py:179-308`; `repository.py:27-43,64-121`; `consumer.py:20-87`; `rabbitmq.py:6-12` |
| Context adapter/workflow | `services/discovery-monitoring/goalpulse_discovery/context_provider.py:60-311`; `workflow.py:30-137`; `probe.py:33-60`; `webhooks.py:1-139` |
| Evidence API/ledger | `services/data-evidence/goalpulse_evidence/api.py:28-149`; `ledger.py:114-124,217-263`; `publishing.py:24-110` |
| Evaluation | `services/goal-evaluation/goal_evaluation/processing.py:1-201`; `consumer.py:20-88`; `evaluators/product.py:124-192`; `evaluators/procurement.py:42-63` |
| Notification | `services/notification/README.md:3-13,60-137`; `notification/consumer.py:39-199`; `notification/reads.py` |
| Agent Integration | `services/agent-integration/README.md:3-15,70-83`; `agent_integration/service.py:49-179`; `agent_integration/packages.py:40-142`; `schemas/action-package.v1.schema.json:43-161` |
| Frontend mode/auth | `frontend/src/lib/api/index.ts:7-17`; `api/mock.ts:8-275`; `api/gateway.ts:24-87`; `api/proposed.ts:4-11`; `lib/auth.ts:1-46`; `playwright.config.ts:33-39` |
| CI | `.github/workflows/contracts.yml:1-21`; `.github/workflows/frontend.yml:21-65`; `.github/workflows/goal-intelligence.yml:15-39` |

This index identifies source locations without reproducing secrets, credentials, raw provider payloads, or personal data.
