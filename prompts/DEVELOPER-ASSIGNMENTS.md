# Four short assignment prompts (copy one to each coding agent)

These are **initial tasks**. Follow full `prompts/START-PHASE2-CODEX.md` and your detailed task file for all acceptance criteria.

## Developer 1
Read AGENTS.md, ADR-0003, contracts v1, `tasks/phase2/issues/R2-D1-00.md`. Implement only R2-D1-00 on own branch; record decision log, additive contract plan, no frozen v1 changes. No real provider calls. Report handoff.

## Developer 2
Read AGENTS.md, ADR-0003, `tasks/phase2/issues/R2-D2-03.md`. Reproduce/fix Node22 frontend production build and Playwright server startup, preserve mock-only CI lane and no hidden mocks in demo mode. Do not edit Gateway. Then stop and report. Later begin R2-D2-01 Anthropic adapter activation.

## Developer 3
Read AGENTS.md, ADR-0003, `tasks/phase2/issues/R2-D3-02.md`. Independently prepare recorded first-party source examples and internal evidence policy tests; once ADR-0003 is merged, implement DE checked decision. No external caller reliability trust; do not modify GE or frozen Observation v1. Then stop and report. Later begin R2-D3-01 real Context main workflow.

## Developer 4
Read AGENTS.md, ADR-0003, `tasks/phase2/issues/R2-D4-01.md`. Prepare genuine HTTP client + RabbitMQ worker integration for Goal Evaluation against accepted internal APIs, using recorded adapters while D3 policy and D1 Gateway PR are pending. Do not bypass checked or edit frozen event schemas. Gate final merge on R2-D3-02 and D1 ADR. Then stop and report. Later begin R2-D4-02 Notification.
