# ADR-0004: Versioned internal evidence-proof transport

- **Status:** Accepted for Developer 3 implementation; D4 consumption deferred
- **Date:** 2026-11-14
- **Supersedes:** no frozen contract

## Context

ADR-0003 requires Data & Evidence to cross-check a normalized product assertion
against a separately captured first-party seller representation. The accepted
`ExtractionSubmission` cannot carry that proof, and using its excerpt would make
the caller's assertion authoritative. The result was a checker that production
could never invoke.

## Decision

Add `contracts/openapi/internal.evidence-proof.v1.yaml` without modifying the
accepted internal v1 document, Observation.v1, or any event. Discovery may submit
one exact persisted extraction plus a separately captured bounded JSON-LD Product
representation to `/internal/v2/extractions/with-proof`. The request must include
public preflight addresses and Context.dev access-policy IDs; Context policies are
also sent on Search/Scrape so provider redirects are checked.

Data & Evidence persists the representation immutably, records the ordinary
Observation as unverified, then runs its own deterministic checker using a stable
check ID. Only the checker can append a checked/disputed Observation and existing
`evidence.updated.v1` outbox event. Replays return the same durable result.

Condition remains outside frozen Observation.v1. DE exposes a bounded authenticated
read of its checker decision for a future D4 change. Until D4 adopts that endpoint,
Goal Evaluation correctly retains its existing `condition=new` unknown result.

## Consequences

- DM and DE require new owned migrations/configuration before readiness.
- Demo requires configured Context access policies and an explicit DE merchant
  hostname registry; missing configuration fails closed.
- D4 must separately adopt the versioned check read before live `condition=new`
  can become satisfied. No current evaluator behavior is weakened.
