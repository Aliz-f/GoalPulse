# Local platform runbook

This Compose stack is for local development only. Gateway is the only browser
application endpoint and is bound to `127.0.0.1:8000`. Service HTTP ports are
container-private. PostgreSQL and RabbitMQ remain loopback-only when published;
their host ports can be changed with `POSTGRES_HOST_PORT`,
`RABBITMQ_HOST_PORT`, and `RABBITMQ_MANAGEMENT_HOST_PORT`. Do not expose these
ports or reuse sample passwords outside local development.

## Start and verify

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

This command declares PostgreSQL, RabbitMQ, migrations, all seven private service
APIs, their P0/P1 workers, and Gateway. Gateway is the only application port
published to the host. Each API has a dependency-aware readiness check; Gateway
readiness is green only after every private API is green. Worker processes use
the existing durable queues and do not publish alternate event names.

Goal Evaluation uses ADR-0005's authenticated, bounded goal-version-to-sources
read from Discovery Monitoring. The platform does not query Discovery's database
or pretend the triggering source is the complete set. Any unavailable, malformed,
over-bound or identity-mismatched response fails closed.

Use `.env.example` to create `.env` and replace every development password.
On the first boot, the PostgreSQL init script creates one non-superuser role
and one database per service. `PUBLIC` is denied `CONNECT` to service
databases; each service role receives `CONNECT` only to its own database.
The bootstrap `POSTGRES_USER` is only for local initialization/admin work and
must not be configured in any service.

After RabbitMQ becomes healthy, the one-shot `rabbitmq-init` service imports
`rabbitmq/definitions.json`. It creates the
durable topic exchange `goalpulse.events`, a durable classic queue for every
declared consumer (five total because the verified-opportunity event fans out
to Notification and Agent Integration), and a dedicated durable DLQ for each.
Classic queue type matches the shipped consumer declarations; changing queue
type requires a coordinated migration because RabbitMQ rejects inequivalent
re-declarations.
Applications must publish persistent messages (`delivery_mode=2`) with
publisher confirms and consume with manual acknowledgement; this is an
application-client setting, not a broker default.

## Gateway and demo profile

The Gateway validates either a signed user bearer token or, only when
`GOALPULSE_PROFILE=demo`, the signed `goalpulse_session` HttpOnly cookie. Demo
startup fails if the two users, session/JWT secrets, frontend gateway mode,
Anthropic configuration, or Context.dev key is missing. Generate a password
hash without putting plaintext on the command line:

```bash
PYTHONPATH=infrastructure/gateway python -c \
  'from getpass import getpass; from goalpulse_gateway.sessions import hash_demo_password; print(hash_demo_password(getpass()))'
```

Put only the resulting `pbkdf2_sha256$...` value in
`GATEWAY_DEMO_USER_A_PASSWORD_HASH` or `GATEWAY_DEMO_USER_B_PASSWORD_HASH`.
The frontend must use credentialed requests, copy the non-HttpOnly
`goalpulse_csrf` cookie into `X-CSRF-Token` for mutations, and never read or
store the session cookie/token.

The accepted Phase 2 owner reads are also exact routes: evaluation history,
notifications/read acknowledgement and action packages. Gateway mints a
60-second owner-bound service JWT with one of `ge.history.read`,
`ns.notifications.read`, `ns.notifications.write`, or `ai.packages.read`.
Private D4 adapters validate that token and never trust a browser owner header.

## Topology smoke check

Use the RabbitMQ management UI or API to verify `goalpulse.events` bindings.
Publishing a valid `contracts/examples/event.goal-activated.json` fixture with
routing key `goal.activated.v1` must arrive only in
`goalpulse.discovery-monitoring.goal.activated.v1`. Stop a manual-ack consumer
before acknowledgement to observe the broker requeue/redelivery flag. Reject
or dead-letter only after the service's bounded retry policy; consumers must
persist inbox/idempotency state before acknowledgement.

## Existing volumes

PostgreSQL initialization runs only for new named volumes. If bootstrap roles
change, preserve any data you need, then have a developer explicitly remove
the local PostgreSQL volume and recreate the stack. Do not use a
volume-removal command against shared or non-local environments. The RabbitMQ
topology import is safe to rerun when the Compose stack starts.
