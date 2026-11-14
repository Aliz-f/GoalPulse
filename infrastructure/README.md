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

This single command currently starts the infrastructure, the owner-provided
Goal Intelligence migration/API, and Gateway. It does **not** yet satisfy the
R2-D1-01 seven-service acceptance criterion: Goal Management runtime composition
is R2-D1-02, and the D3/D4 services still need their owners' production
factories/worker commands. Gateway liveness is available, but readiness remains
`503 not_ready` until both GI and GM respond successfully. The Gateway does not
substitute fake ready services.
See `docs/phase2/R2-D1-01-GATEWAY-HANDOFF.md` for the exact status.

Use `.env.example` to create `.env` and replace every development password.
On the first boot, the PostgreSQL init script creates one non-superuser role
and one database per service. `PUBLIC` is denied `CONNECT` to service
databases; each service role receives `CONNECT` only to its own database.
The bootstrap `POSTGRES_USER` is only for local initialization/admin work and
must not be configured in any service.

After RabbitMQ becomes healthy, the one-shot `rabbitmq-init` service imports
`rabbitmq/definitions.json`. It creates the
durable topic exchange `goalpulse.events`, a durable quorum queue for every
declared consumer (five total because the verified-opportunity event fans out
to Notification and Agent Integration), and a dedicated durable DLQ for each.
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
