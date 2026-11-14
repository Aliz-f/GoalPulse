# Copy-paste prompt for Developer 1 / Tech Lead — FIRST TASK

Implement task **R2-D1-00** from the GoalPulse Phase 2 decided package. Read AGENTS.md and current accepted ADRs, actual current source/contract files, then the **new** `docs/adr/0003-phase2-mvp-decisions.md`, `docs/phase2/CONTRACT-CHANGE-PLAN.md`, and `tasks/phase2/issues/R2-D1-00.md`.

The user (product owner) explicitly delegated MVP policy decisions. You may integrate **those decisions** via the standard contract + ADR PR. Do not request another full-team decision meeting. You **MUST** stop only where a decision violates a frozen accepted contract or creates material security risk; document exact file:line and raise a small scoped issue instead of silently changing behavior.

1. Show current git HEAD/status, the audit baseline comparison, and selected decisions. Do not reset unrelated changes.
2. Import ADR-0003 and update current `docs/implementation/DECISIONS.md` with approved-for-MVP choices and PR link. Existing accepted ICP-01/08/13 stay accepted.
3. Add controlled, additive versioned contract extensions for evaluation history, notifications, demo session; P1 package uses existing D4 grouped `seller_orders` schema, not the older `seller_offers` draft. Do NOT change frozen v1 files or Rabbit event routing.
4. Define Gateway demo cookie identity and service JWT forwarding, only for isolated demo profile; never publish internal service ports.
5. Update `AGENTS.md` by APPENDING Phase2 addendum, not overwriting existing policy.
6. Run contract tests, add two-user 401/403/404 contract examples, and report exact changed files, migration impact and dependencies.
7. After this task passes, publish a short handoff for D2/D3/D4 (route names, auth, env-key names, expected DTO shape); do not start Gateway implementation in the same PR.

Return PASS/FAIL/NOT RUN for every acceptance criterion. No .env/key output, no chargeable provider calls.
