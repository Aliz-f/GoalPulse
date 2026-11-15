# ADR-0005: Complete goal-version source lookup

- **Status:** Accepted by Developers 1, 3 and 4
- **Date:** 2026-11-15
- **Supersedes:** no frozen contract

## Context

Goal Evaluation receives `evidence.updated.v1` for one source, but must evaluate
the complete candidate set linked to the current immutable goal version. The
accepted internal API exposes source-to-goal links only. Assuming the triggering
source is the whole set or reading Discovery Monitoring's database would produce
incomplete evaluations or violate service ownership.

## Decision

Add `contracts/openapi/internal.goal-sources.v1.yaml` without modifying the
accepted internal v1 document, frozen JSON schemas, or event routing. Discovery
Monitoring exposes an authenticated read for one exact activated goal version.

The response contains all active source IDs, sorted lexicographically, with no
duplicates and no truncation. ADR-0003 limits the set to three, so the contract
uses `maxItems: 3` and does not paginate. A durable activation job establishes a
known goal version. A known version with no active links returns an empty array;
a version never activated in Discovery Monitoring returns 404. Data outside the
bound fails closed rather than being silently truncated.

Goal Evaluation supplies the accepted `dm.sources.read` ServiceBearer scope and a
UUID correlation ID. It validates the exact goal/version identity, UUIDs, order,
uniqueness and bound before evaluation.

## Consequences

- Discovery Monitoring remains the only owner of source-link state.
- Goal Evaluation can process complete current-version evidence without a new
  event or cross-service SQL.
- Existing source-to-goal consumers and all frozen v1 artifacts are unchanged.
- Readiness no longer needs a temporary approval flag once this coordinated
  contract and both bindings are deployed.
