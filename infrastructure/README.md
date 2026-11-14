# Local platform runbook

This Compose stack is for local development only. PostgreSQL is bound to
`127.0.0.1:5432`; RabbitMQ AMQP and management are bound to `127.0.0.1:5672`
and `127.0.0.1:15672`. Do not expose these ports or reuse the sample passwords
outside local development.

## Start and verify

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

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
