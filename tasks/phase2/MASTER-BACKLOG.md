# Phase 2 Master Backlog — decided implementation tasks

This tracks implementation tasks based on Codex audit commit `5186edf` and delegated MVP decisions in ADR-0003. **Status is PLANNED until actual repo PRs pass**, not a claim of completion. `R2-D1-00` creates the authoritative repo ADR/contract baseline; frozen v1 remains unchanged.

| Task | Owner | Priority | Gate | Dependencies | Title |
|---|---|---|---|---|---|
| `R2-D1-00` | D1 | P0 | R0 | — | Apply decided MVP policies and additive contract PR |
| `R2-D1-01` | D1 | P0 | R1 | `R2-D1-00` | Runnable Gateway, demo authentication and Compose stack |
| `R2-D1-02` | D1 | P0 | R1 | `R2-D1-01` | Goal Management production runtime and truthful projection |
| `R2-D1-03` | D1 | P0 | R3/R4 | `R2-D1-02`, `R2-D2-01`, `R2-D2-02`, `R2-D2-04`, `R2-D3-01`, `R2-D3-02`, `R2-D3-03`, `R2-D4-01`, `R2-D4-02` | Full-stack integration, outages, security and release evidence |
| `R2-D2-03` | D2 | P0 | R0 | — | Fix Node 22 frontend build and Playwright startup |
| `R2-D2-01` | D2 | P0 | R1 | `R2-D1-00` | Real Anthropic LLM and safe fail-closed Goal Intelligence |
| `R2-D2-02` | D2 | P0 | R1/R2 | `R2-D1-01`, `R2-D2-03` | Real browser Gateway client and session handling |
| `R2-D2-04` | D2 | P0 | R3 | `R2-D2-02`, `R2-D4-02` | Truthful My Goals and notification views |
| `R2-D3-02` | D3 | P0 | R0/R1 | `R2-D1-00` | Implement internal `checked` evidence policy (ICP-09) |
| `R2-D3-01` | D3 | P0 | R1/R2 | `R2-D1-00` | Wire real Context.dev activation→Evidence production pipeline |
| `R2-D3-03` | D3 | P0 | R3 | `R2-D3-01` | Periodic scans, operator check-now and cost guard |
| `R2-D4-01` | D4 | P0 | R1/R2 | `R2-D1-00`, `R2-D3-02` | Runnable GE worker, real readers, exact current-version evaluation API |
| `R2-D4-02` | D4 | P0 | R3 | `R2-D4-01`, `R2-D1-00` | Real in-app notification worker, API and business dedup |
| `R2-D4-03` | D4 | P1 | R3/R4 | `R2-D4-01`, `R2-D1-00` | Wire read-only Agent Integration and grouped seller_orders DTO |
| `R2-D4-04` | D4 | P1 | R4+ | `R2-D4-01`, `R2-D3-01`, `R2-D3-02` | Real recurring café procurement normalization and solver |

## Start immediately (parallel)

- **D1:** `R2-D1-00` → `R2-D1-01`; notify others once Gateway/service-identity contract available.
- **D2:** `R2-D2-03` independently; implement Anthropic `R2-D2-01` against existing GI interface once ADR applied.
- **D3:** prepare checked-evidence tests, implement `R2-D3-02` after ADR; create Context client/DE adapters for `R2-D3-01` against accepted internal API.
- **D4:** implement GE worker typed adapters/test fixtures for `R2-D4-01` while D3 checker policy is being applied; merge only after requirements pass.

## Hard completion ordering

`R2-D1-00` → `R2-D1-01` + D2/D3/D4 service runtime work → `R2-D1-02` and main pipeline → `R2-D3-03` + `R2-D4-02` + `R2-D2-04` → `R2-D1-03` release.

**Optional only after P0:** `R2-D4-03` action package and `R2-D4-04` café basket. No travel support and no auto purchase.

## Every ticket's Definition of Done

- Current branch/status checked, assigned owner paths only.
- Unit+recorded provider test (and isolated integration if applicable) PASS.
- Domain/event/REST contract tests match accepted schemas and the new additive contract PR.
- Mock fixture unavailable in demo user-facing path; no silent fallback.
- Explicit per-acceptance PASS/FAIL/NOT RUN log with test commands, handoff and redacted evidence.
- Do not report code as working from proposal alone.
