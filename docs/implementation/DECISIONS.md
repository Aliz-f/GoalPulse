# GoalPulse implementation decision log

No proposed changes in the implementation blueprint have been approved merely by being included in this starter kit. Record human/team signoff here.

| Date (UTC) | Decision ID | Choice made | Approved by | PR / Issue |
|---|---|---|---|---|
| 2026-11-14 | ICP-01 | Accepted: D1, D2, D3 and D4 approved the reviewed internal HTTP contract. `internal.proposed.v1.yaml` is retained under its historical filename but has accepted version 1.0.0 status; see ADR-0002. | Developers 1–4 | Reviewed approval PR; ADR-0002 |
| 2026-11-14 | ICP-08 | Accepted: D1 and D2 approved ServiceBearer JWT machine-to-machine transport. D1-PL-03 owns the concrete issuer/audience/signature/expiry implementation and security tests. | Developers 1–2 | Reviewed approval PR; ADR-0002 |
| 2026-11-14 | ICP-13 | Accepted Phase 1 handling: `Observation.v1` cannot prove product condition or arbitrary required attributes. D4 returns `unknown` for missing condition evidence and never verifies a `new` offer from v1 alone. `Observation.model` is usable only with an explicit exact-model mapping. A future versioned evidence extension requires a separate ICP; no v1 schema change is approved. | Developers 1 and 4 | Approved remediation for PR #6; versioned extension follow-up pending |
