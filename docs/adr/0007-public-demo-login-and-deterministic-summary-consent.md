# ADR-0007: Public demo login and deterministic summary consent

- **Status:** Accepted for the controlled demo
- **Date:** 2026-11-15
- **Approved by:** Product owner / Developer 1

## Context

The controlled MVP must be usable by a reviewer without receiving credentials
through a separate channel. The previous deployment stored only private demo
password hashes, so the sign-in screen could not provide working defaults.

Goal Intelligence also delegated the final canonical-summary acknowledgement to
LLM extraction. Clear replies such as `deal` could therefore leave a complete
draft in `collecting`, with no deterministic control for the user to advance to
the exact-revision confirmation step.

## Decision

The two isolated demo accounts are intentionally public and must never carry
private or production data. Their usernames and public demo passwords are
compiled into the demo frontend and displayed as selectable sign-in options.
The centralized environment manager derives salted PBKDF2-SHA256 hashes from
those public passwords. Only the hashes reach the Gateway container; API keys,
session secrets, JWT secrets, database passwords, and broker credentials remain
server-only.

The frontend provides an explicit **Approve this summary** action through the
existing versioned message endpoint. Goal Intelligence recognizes a narrow set
of exact affirmative replies, including the button's fixed message and `deal`,
only when `summary_approval` is the current canonical question. A reply that
contains changes still goes through Anthropic extraction. Summary approval only
makes the draft ready: Goal Management continues to require a separate explicit
confirmation of the displayed immutable revision before monitoring starts.

## Consequences

- These credentials are public by design. Gateway rate limiting, account
  isolation, short sessions, and demo-data cleanup remain mandatory.
- This profile is not suitable for production or private customer data.
- Changing either public demo password requires running `manage_env.py`, then
  rebuilding the frontend and restarting Gateway so the displayed value and
  server-side hash change together.
- No frozen schema, route, event, or public DTO changes.
- LLM/provider failure can no longer block an otherwise complete draft's
  explicit summary acknowledgement.
