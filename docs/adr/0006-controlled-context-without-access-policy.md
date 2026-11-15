# ADR-0006: Controlled Context.dev demo without an access policy

- Status: Accepted
- Date: 2026-11-15
- Decision owners: Product owner and Developer 1
- Scope: Junction Helsinki controlled MVP deployment

## Context

The deployment account has a valid Context.dev API key and credit, but the
provider has not enabled Web Access Policies for the organization. GoalPulse's
existing demo startup gate required a provider-issued `pol_...` ID. That gate
prevented all real Context.dev discovery even though the provider API permits a
request with no `policies` field.

Using a fabricated policy ID, silently switching to fixtures, or treating an
uncontrolled capture as checked evidence would violate the repository's
provenance and no-fabrication rules. Running the frontend in mock mode would also
fail the goal of a real interactive MVP.

## Decision

Add `DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY`, defaulting to `false`.

When the profile is `demo`, an operator may set it to `true` and leave
`CONTEXT_DEV_ACCESS_POLICY_IDS` empty. Discovery Monitoring then uses the real
Context.dev API and deliberately omits the request `policies` field. Anthropic,
Gateway, Goal Management, the broker, databases, and all downstream services
remain real. There is no fixture or provider fallback.

This exception changes startup admission only. Existing URL validation, public
DNS checks, same-host redirect handling, merchant allowlists, source limits,
per-user quotas, and operation budgets remain enabled.

A provider capture made without a policy cannot supply the proof required by the internal
Data & Evidence checker. It is stored as `unverified`; evaluation must remain
Candidate/Unknown and cannot emit a verified opportunity or verified
notification from that capture. The UI may truthfully show the discovered
candidate and missing-proof state.

Once Context.dev enables policies, the operator must configure the real
`pol_...` ID and reset `DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY=false`.

## Consequences

- The Junction MVP can exercise real user interaction, Anthropic clarification,
  goal confirmation, real Context.dev search/scrape, durable evidence ingestion,
  and truthful Unknown results.
- The controlled deployment does not falsely claim a fully verified live
  opportunity while provider-side policy proof is unavailable.
- Provider-side outbound restrictions are weaker during the exception, so this
  mode is not approved as a general public-production posture.
- Frozen v1 schemas, public APIs, events, and the internal checked-evidence rule
  are unchanged.
