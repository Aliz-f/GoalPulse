# GoalPulse — Coding Agent Task Brief Template

Copy this template into an issue or agent prompt for **each** coding task. The repository's `AGENTS.md` applies throughout.

```markdown
## Task ID and title
GP-XXX — <concise outcome>

## Assigned developer / service owner
Developer <1|2|3|4> — <service name>

## Objective (observable behavior)
<What must work, not what code to write>

## Allowed write paths
- services/<owned-service>/**
- tests/<owned-or-approved-tests>/**

## Read-only reference paths
- AGENTS.md
- README.md
- docs/architecture.md
- docs/data-model.md
- contracts/**

## Required inputs/contracts
- <exact schema filename and relevant fields>
- <exact event routing keys or API paths>

## Expected output and interfaces
- <endpoint/handler/event behavior>
- <data persistence owned by this service>

## Acceptance tests
1. <happy path>
2. <invalid/unknown data>
3. <duplicate event or stale version, if relevant>
4. <security/ownership boundary, if relevant>

## Required validation commands
python scripts/validate_contracts.py
python -m pytest -q
<service-specific commands>

## Explicit non-goals
- No modifications to contracts/** or other service source files
- No new dependencies unless approved
- No real purchase/booking/payment

## Dependencies / mocks
- <fixture filenames or mock HTTP responses>

## Deliverable
Implementation + tests + changed-files list + contract-impact statement + risks.

## Change-control rule
If the task cannot be completed without modifying a frozen contract or another
service's files, stop that portion and submit an Interface Change Proposal.
```

## Interface Change Proposal template

```markdown
### ICP-XXX — Requested interface change
- Existing authoritative artifact / version:
- Motivation and reproducible issue:
- Exact proposed diff (schema/event/API):
- Breaking or additive? Why?:
- Producer(s) affected:
- Consumer(s) affected:
- Version/migration and rollout order:
- Fixtures and contract tests to add/update:
- Developer 1 approval:
- Affected service-owner approvals:
- Corresponding ADR / PR:
```
