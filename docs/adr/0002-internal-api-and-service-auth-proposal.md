# ADR-0002: Internal API and service authentication

- **Status:** Accepted
- **Date:** 2026-11-14
- **Related change proposals:** ICP-01, ICP-08

## Context

The frozen public Gateway v1 contract and the four frozen event routes do not
define authenticated service-to-service reads. The historical filename
`contracts/openapi/internal.proposed.v1.yaml` now contains the accepted shared
contract for those reads and writes. It keeps its original filename solely for
repository compatibility; the document's version and status identify it as
accepted.

## Decision

1. Internal routes use `/internal/v1/` only, on a private network, and never
   accept a browser-supplied `X-User-Id` as proof of identity.
2. Each call uses a validated service bearer token. D1-PL-03 owns the
   concrete issuer, audience, signature/JWKS validation, expiry, service
   subject and per-operation authorization implementation. A user token is
   not a substitute for a service identity.
3. Every caller propagates an RFC 4122 `X-Correlation-ID`; IDs and boundary
   timestamps remain UUID and UTC RFC 3339 values. No response may contain
   provider credentials, raw scrape content, or a user transcript.
4. Writes are idempotent through a durable request/attempt identity. The
   proposed `attempt_id` is the extraction idempotency key. A reused key with
   different content is `409`; a safe replay returns the durable result.
5. Goal Intelligence candidate reads and confirmation marks are GM-only.
   Goal Management runtime/version/active reads are restricted to the
   consuming services with a demonstrated need. Data Evidence ingestion is
   DM-only. Evaluation and action-package reads require an explicitly
   authorized internal consumer. Authorization remains independent of any
   `owner_user_id` returned by a resource.
6. The proposal introduces no new RabbitMQ routing key. Producers and
   consumers continue to use only the four frozen v1 event types in
   `contracts/event-routing.yaml`.

## Review outcomes and implementation follow-up

| Area | Review outcome | Follow-up owner |
|---|---|---|
| ICP-08 identity | Service JWT is the accepted transport model; browser-supplied user identity is forbidden. | D1-PL-03 implements issuer/audience/signature/expiry checks and scoped test identities. |
| Failure behavior | The reviewed responses and common error rules are accepted for the MVP. | Each service adds operation-level authorization, validation and stale-data tests. |
| Correlation | `X-Correlation-ID` propagation is mandatory across HTTP and event hops. | D1-PL-03 and each service enforce it at boundaries. |
| Consent and owner data | Callers receive only data required by their approved operation; active-goal reconciliation excludes owner details. | Service owners implement least-privilege authorization. |
| Action package | The prepare-only route remains non-public; its richer package shape stays an ICP-02 follow-up. | D4 implements only the accepted prepare-only guarantees. |
| Mock fixtures | The OpenAPI schemas and existing frozen DTO examples are the shared mock source. | Consumers add operation fixtures in their owned tests before implementation. |

## Affected interfaces

- GI → GM: ready exact-revision candidate read and idempotent confirmation mark.
- GM → DM/GE/NS/AI: immutable goal version, current runtime, and paginated
  active-goal reconciliation reads.
- DM → DE: idempotent extraction submission; missing price is represented by
  a `202` extraction attempt, never a fabricated Observation.
- DM/DE/GE/AI: source subscriptions, observations, evaluations, and future
  prepare-only action-package reads.

## Approval record

| Proposal | Required approvers | Current status |
|---|---|---|
| ICP-01 | D1, D2, D3, D4 | Approved by all required owners via the reviewed PR. |
| ICP-08 | D1, D2 | Approved by all required owners via the reviewed PR. |

The accepted OpenAPI document is the authoritative internal HTTP contract for
the listed operations. Services still use deterministic mocks for dependencies
that have not yet been implemented.

## Consequences

No frozen v1 schema, Gateway operation, or event route changes. D1-PL-02,
D1-PL-03, and dependent internal API work may now proceed against this
accepted contract; the contract itself does not imply that any endpoint is
already implemented.
