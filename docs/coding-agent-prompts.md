# Copy/paste prompts for the four coding agents — Phase 1

**Shared instruction for all agents:** Read `README.md`, `docs/architecture.md`, and every schema under `contracts/schemas/` first. Do not introduce invented live prices, hidden auth bypasses, or auto-purchase. Do not commit secrets. All changes touching `contracts/` require Developer 1 review. Run `python scripts/validate_contracts.py` and `python -m pytest -q` before a PR.

## Developer 1 — Technical lead / contracts owner

> You are lead maintainer for GoalPulse Phase 1. Inspect the starter repository; enforce frozen v1 JSON Schemas, events and gateway OpenAPI. Verify Docker Compose and local Postgres/RabbitMQ, branch protection plan, CI schema validation, separate DB ownership policy and docs consistency. Do not implement business services yet. Record any necessary breaking contract change as a proposed v2 in an ADR instead of silently editing v1. Output exact manual test commands and open issues for Phase 2. Never commit `.env`.

## Developer 2 — Goal Intelligence + UX contracts

> Using `contracts/schemas/goal-draft.v1.schema.json` and `goal-spec.v1.schema.json`, design three chat dialogues: missing shipping/taxes, product model ambiguity, and monthly café order. For each turn output expected GoalDraft revisions and exactly one next clarification question. Design the confirmation summary, including explicit approval and expected revision. Sketch Frontend routes `new-goal`, `goal-confirmation`, `my-goals`. Do not invent fields absent from schemas; propose changes via Dev 1. Provide fixtures + acceptance scenarios, not an unfinished LLM implementation.

## Developer 3 — Context.dev feasibility + normalization

> Read `docs/context-dev-mapping.md`. Privately test Context.dev scrape on two public pages with a personal API key in environment only. For each record whether price, variant, inventory, shipping-to-Finland, VAT and cache age are observable. Redact credentials and personal data. Map one actual output into `observation.v1.schema.json` without inventing missing values. If a required price cannot be observed, record a failure rather than producing fabricated JSON. Present a go/no-go decision for each merchant. Do not create real Monitors unless their costs and monitored URLs are agreed.

## Developer 4 — Evaluation specification + contract tests

> Read `goal-spec.v1.schema.json`, `observation.v1.schema.json` and `goal-evaluation.v1.schema.json`. Design acceptance cases for exact model, wrong product, over budget, missing shipping, unknown stock, stale evidence and duplicate events. Include integer-cents arithmetic and explicit handling of `unknown`. Ensure `is_opportunity_verified` can only be true when every mandatory criterion has satisfied evidence and matching goal version. Add tests and test fixtures without changing production contracts. No live price assumptions and no financial side effects.
