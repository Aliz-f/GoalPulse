# GoalPulse implementation decision log

No proposed changes in the implementation blueprint have been approved merely by being included in this starter kit. Record human/team signoff here.

| Date (UTC) | Decision ID | Choice made | Approved by | PR / Issue |
|---|---|---|---|---|
| 2026-11-14 | ICP-01 | Accepted: D1, D2, D3 and D4 approved the reviewed internal HTTP contract. `internal.proposed.v1.yaml` is retained under its historical filename but has accepted version 1.0.0 status; see ADR-0002. | Developers 1–4 | Reviewed approval PR; ADR-0002 |
| 2026-11-14 | ICP-08 | Accepted: D1 and D2 approved ServiceBearer JWT machine-to-machine transport. D1-PL-03 owns the concrete issuer/audience/signature/expiry implementation and security tests. | Developers 1–2 | Reviewed approval PR; ADR-0002 |
| 2026-11-14 | ICP-13 | Logged as deferred after Phase 1: `Observation.v1` cannot prove product condition or arbitrary attributes. D4 must return `unknown` for missing evidence and must not verify a `new` GPU offer. No frozen contract change is approved. | Developer 1 | Pending D3/D4 review; no ADR or schema change yet |
