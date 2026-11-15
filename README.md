# GoalPulse

GoalPulse is a goal-monitoring MVP for `product_purchase` and
`recurring_procurement` goals. A user clarifies and confirms a goal, the platform
discovers and checks evidence, evaluates it deterministically, and prepares
notifications and handoff packages. It never purchases, pays, or books on the
user's behalf.

## Release status

The repository now has one containerized stack for the frontend, Gateway, seven
private services, workers, migrations, PostgreSQL, and RabbitMQ. One root `.env`
is the runtime configuration source and one Compose command builds and starts the
whole application. Nginx and TLS run on the VPS host, outside Docker.

This is a **Phase 2 controlled demo release**, not an unrestricted public
production launch. The accepted architecture supports only `test`, `dev`, and
`demo` profiles. Demo authentication uses two explicitly configured local users;
it is not an external identity provider. Before a public launch, complete a
separate production-auth, privacy, security, observability, backup, and incident
response review.

Live discovery normally requires a Context.dev outbound access-policy ID
beginning with `pol_`. For the controlled Junction demo only, ADR-0006 permits
the explicit `DM_ALLOW_CONTEXT_WITHOUT_ACCESS_POLICY=true` exception. It still
uses the real provider and never falls back to fixtures, but captures remain
unverified and cannot produce a verified opportunity until a real policy is set.

## Architecture

Browser traffic reaches host Nginx over HTTPS. Nginx proxies page and asset
requests to the frontend on `127.0.0.1:3000`, and `/v1`, `/v2`, and `/health`
requests to Gateway on `127.0.0.1:8000`. Gateway is the only public API boundary.
All service APIs remain on private Docker networks.

| Component | Responsibility |
|---|---|
| Frontend | Next.js conversation, goal, evidence, evaluation, and notification UX |
| Gateway | Public API, ownership checks, demo sessions, CSRF, internal service tokens |
| Goal Intelligence | Anthropic-backed clarification and draft revisions |
| Goal Management | Confirmation, immutable versions, lifecycle, transactional outbox |
| Discovery Monitoring | Context.dev discovery, source registry, schedules and cost guards |
| Data & Evidence | Source checking, normalization and evidence provenance |
| Goal Evaluation | Deterministic tri-state evaluation |
| Notification | Idempotent notifications and acknowledgement |
| Agent Integration | `prepare_only` handoff packages |
| PostgreSQL / RabbitMQ | Service-owned persistence and durable topic messaging |

The authoritative external interfaces remain under [`contracts/`](contracts/).
See [`docs/architecture.md`](docs/architecture.md),
[`docs/data-model.md`](docs/data-model.md), and
[`docs/phase2/ARCHITECTURE.md`](docs/phase2/ARCHITECTURE.md) for boundaries and
invariants.

## Quick start: complete Docker stack

Requirements: Docker Engine with the Compose v2 plugin, Python 3.12+ for local
validation, and Node.js 22 only if running frontend checks outside Docker.

```bash
python3 infrastructure/release/manage_env.py
python3 infrastructure/release/manage_env.py --check
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build --wait --wait-timeout 300
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

For a local non-live build, set `GOALPULSE_PROFILE=dev`,
`NEXT_PUBLIC_API_MODE=mock`, and `NEXT_PUBLIC_GATEWAY_URL=`. The backend still
requires a nonempty Context.dev key configuration because Discovery fails closed;
tests inject deterministic provider transports and do not make live calls.

Default loopback endpoints:

- Frontend: `http://127.0.0.1:3000`
- Gateway readiness: `http://127.0.0.1:8000/health/ready`
- PostgreSQL: `127.0.0.1:5432`
- RabbitMQ AMQP: `127.0.0.1:5672`
- RabbitMQ management: `http://127.0.0.1:15672`

All published ports are explicitly loopback-bound. Override the host-side port
variables in `.env` when a development machine already uses a default port.

Stop containers without deleting persistent data:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env stop
```

Do not run a volume-removal command unless the named databases and broker state
are confirmed disposable and backed up.

## Centralized environment

`.env.example` is the complete, domain-grouped template for Compose. `.env` is
ignored by Git and must remain mode `0600`. It contains deployment/ports,
frontend public settings, per-service database passwords, RabbitMQ credentials,
Gateway and service identity, demo users, Anthropic, Context.dev, evidence, and
notification configuration.

Every template variable has `Description` and `Options` comments. The manager
creates `.env` when it does not exist, preserves configured values, and prompts
one at a time only for invalid, empty-required, or placeholder values:

```bash
python3 infrastructure/release/manage_env.py
```

At each prompt, enter a replacement, press Enter to retain the current value,
enter `s` to skip it until a later run, or enter `-` to clear it. For generated
secrets and demo UUIDs, enter `g`. Secret input and
existing secret values are hidden. A skipped or still-invalid setup is saved
with mode `0600` and exits with status 2, so rerunning the command resumes the
unfinished configuration. Use `--all` to review every variable, repeated
`--only NAME` arguments to manage selected variables, and `--check` for a
non-interactive, redacted readiness check. Add `--generate-random` to replace
all invalid or placeholder database/broker/JWT/session secrets and demo UUIDs
without displaying their values. Provider API keys, access-policy IDs, public
origins, merchant hosts, and model IDs must come from the operator/provider and
are never fabricated. The two `DEMO_USER_*_PUBLIC_PASSWORD` values are
intentionally visible on the isolated demo sign-in page. The manager derives
the matching salted Gateway password hashes automatically.

Frontend runtime selectors, the public origin, and the two isolated demo login
pairs are public by design. API keys, private passwords, session secrets, and
service JWT secrets are never passed to the frontend build.
Changing database or RabbitMQ bootstrap credentials in `.env` does not rotate
credentials already persisted in an existing volume; follow the VPS runbook.

Validate configuration without printing values:

```bash
python3 infrastructure/release/manage_env.py --check
python3 infrastructure/release/validate_env.py --env-file .env
```

## VPS deployment

Follow [`docs/phase2/VPS-DEPLOYMENT.md`](docs/phase2/VPS-DEPLOYMENT.md) from the
first server package through DNS, secrets, database initialization, one-command
startup, host Nginx, Let's Encrypt, firewalling, verification, backups, updates,
and rollback. The host configuration template is
[`infrastructure/nginx/goalpulse.conf.example`](infrastructure/nginx/goalpulse.conf.example).
There is deliberately no Nginx service in Compose.

## Validation and release gates

Run repository checks from the root:

```bash
python scripts/validate_contracts.py
python -m pytest -q
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
env NEXT_PUBLIC_GOALPULSE_PROFILE=dev NEXT_PUBLIC_API_MODE=mock \
  NEXT_PUBLIC_GATEWAY_URL= npm --prefix frontend run build
```

R2-D1-03 coordinates recorded, durable, frontend, and controlled-live evidence:

```bash
python infrastructure/release/run_phase2_gate.py --list
python infrastructure/release/run_phase2_gate.py \
  --lane recorded --output /tmp/goalpulse-recorded-release.json
```

The recorded lane removes provider credentials and cannot satisfy the live
provider requirement. See
[`docs/phase2/R2-D1-03-RUNBOOK.md`](docs/phase2/R2-D1-03-RUNBOOK.md) and
[`docs/phase2/R2-D1-03-HANDOFF-BLOCKERS.md`](docs/phase2/R2-D1-03-HANDOFF-BLOCKERS.md).

## Domain guardrails

- Only `product_purchase` and `recurring_procurement` are supported in v1.
- Money is integer EUR cents; missing evidence remains `unknown`.
- A verified opportunity is evidence at observation time, not a completed order.
- Evidence retains source IDs, URLs, observed timestamps, goal versions, and
  evaluation conditions.
- User confirmation is mandatory and stale revisions return conflict errors.
- Handoffs are `prepare_only`; `auto_purchase` remains false.
- The frontend calls Gateway only. Services never read another service's database.

## Contributor map

- Repository policy: [`AGENTS.md`](AGENTS.md)
- Implementation index: [`docs/implementation/README.md`](docs/implementation/README.md)
- Backlogs: [`tasks/MASTER_BACKLOG.md`](tasks/MASTER_BACKLOG.md) and
  [`tasks/phase2/MASTER-BACKLOG.md`](tasks/phase2/MASTER-BACKLOG.md)
- Accepted ADRs: [`docs/adr/`](docs/adr/)
- Infrastructure details: [`infrastructure/README.md`](infrastructure/README.md)
- Frontend modes: [`frontend/README.md`](frontend/README.md)
