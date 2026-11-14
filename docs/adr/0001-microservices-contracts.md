# ADR-0001: Contract-first event-driven microservices

- **Status:** Accepted for the hackathon prototype
- **Date:** 2026-11-14

## Decision

Use seven independently owned services, monorepo, RabbitMQ topic exchange, separate PostgreSQL development databases, OpenAPI for synchronous endpoints and JSON Schema for persisted events and DTOs. The Gateway is routing infrastructure rather than an eighth business service. The identity provider is external/not implemented in Phase 1.

## Rationale

Four people with coding agents can work concurrently if DTO/event shapes are stable. Contracts are versioned and tested in CI. A single repository reduces integration friction while retaining deployable service boundaries.

## Trade-offs

Higher operational and distributed-system overhead than a modular monolith, delayed consistency for dashboard status, and need for observability/outbox/idempotency. Do not add separate brokers or caches until required.

## Changes

Breaking DTO or event changes create `v2`; old consumers continue to accept `v1` until migration. Developer 1 reviews edits to contracts. Consumers ignore unknown additive fields only when permitted by the schema; the current strict v1 schema intentionally requires coordinated revisions.
