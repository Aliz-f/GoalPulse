# GoalPulse platform infrastructure

`infrastructure/docker-compose.yml` is the single application stack for local
development, CI verification, and the controlled VPS demo. It contains:

- Next.js frontend and public FastAPI Gateway;
- all seven private service APIs;
- service-owned migration jobs and RabbitMQ workers;
- PostgreSQL with one logical database/role per service;
- RabbitMQ with the frozen topic exchange, durable queues, and DLQs.

Nginx is not a Compose service. On a VPS it runs on the host and uses
`nginx/goalpulse.conf.example` to proxy HTTPS to the loopback frontend and
Gateway ports.

## Central configuration

The root `.env` is the only Compose environment file. `.env.example` documents
the description and accepted options for every `${...}` Compose input. Use the
interactive manager to create or repair `.env`:

```bash
python3 infrastructure/release/manage_env.py
python3 infrastructure/release/manage_env.py --check
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
```

By default, the manager asks one by one only for required empty values, invalid
values, and placeholders. Enter `s` to skip a variable and resume it on a later
run. Enter `g` to generate application secrets, UUIDs, or password hashes when
offered. Use `--all` to review everything or repeat `--only NAME` to manage
selected variables. `--generate-random` fills every invalid or placeholder
database/broker/JWT/session secret and demo UUID; it never fabricates external
API keys, policy IDs, domains, or model IDs. Secret values and secret input are
never displayed, and the written file is forced to mode `0600`.

The validator never displays values. In `demo`, it fails closed unless the
frontend uses Gateway, the public HTTPS origins match, demo identities are
complete, Anthropic and Context.dev are selected, and evidence fixtures are
disabled. A real `pol_...` provider policy is required by default. ADR-0006's
explicit controlled-demo exception may omit it, but resulting evidence remains
unverified.

The frontend receives only three public build arguments: profile, API mode, and
public Gateway origin. API keys, DB/broker passwords, session secrets, and JWT
secrets are not passed into its image.

## Start the whole stack

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env \
  up -d --build --wait --wait-timeout 300
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

Default host bindings are:

| Endpoint | Binding | Exposure |
|---|---|---|
| Frontend | `127.0.0.1:3000` | Host Nginx or local browser |
| Gateway | `127.0.0.1:8000` | Host Nginx or local diagnostics |
| PostgreSQL | `127.0.0.1:5432` | Operator/local tooling only |
| RabbitMQ AMQP | `127.0.0.1:5672` | Operator/local tooling only |
| RabbitMQ management | `127.0.0.1:15672` | SSH tunnel/local only |

Override host-side ports in `.env` when necessary. Service APIs use `expose`
only and remain on private Docker networks. Gateway and frontend cannot join the
data-only network.

Check the integrated application:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:3000/
```

Stop without deleting data:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env stop
```

Do not run `down -v` against retained data.

## Bootstrap behavior

On a new PostgreSQL volume, `postgres/01-create-service-dbs.sh` creates one
non-superuser role and one database per service. `PUBLIC` cannot connect to
service databases; each service role receives only its own database access. The
bootstrap admin is not used by application containers.

After RabbitMQ is healthy, the one-shot `rabbitmq-init` job imports
`rabbitmq/definitions.json`. It declares `goalpulse.events`, per-consumer durable
classic queues, and dedicated DLQs. Applications use persistent publication,
publisher confirms, manual acknowledgements, bounded retry, inbox deduplication,
and transactional outboxes where state and publication intent must be atomic.

Initialization scripts run only for a new volume. Changing DB or RabbitMQ
passwords in `.env` does not rotate credentials stored in an existing volume.

## Profiles

- `test`: deterministic tests with explicit fixtures/transports.
- `dev`: local development; frontend may explicitly use `mock`.
- `demo`: real Gateway, Anthropic, Context.dev, and Data & Evidence paths only.

There is no approved `prod` profile. A demo provider failure remains visible and
never silently switches to fixtures. Context.dev access-policy IDs are required
by default; the default-off ADR-0006 exception permits real discovery without a policy
for the controlled Junction demo without promoting that evidence to checked.

## Release gate

List or run R2-D1-03 lanes:

```bash
python3 infrastructure/release/run_phase2_gate.py --list
python3 infrastructure/release/run_phase2_gate.py \
  --lane recorded --output /tmp/r2-d1-03-recorded.json
```

The recorded lane clears provider credentials and writes a redacted report. The
durable lane uses explicitly disposable PostgreSQL/RabbitMQ resources. The live
lane remains a separately authorized manual smoke; recorded fixtures cannot
satisfy it. Full instructions are in `docs/phase2/R2-D1-03-RUNBOOK.md`.

## VPS deployment

Use `docs/phase2/VPS-DEPLOYMENT.md`. It covers Docker installation references,
central secrets, one-command start, host Nginx/Let's Encrypt, firewalling,
verification, backup, update, rollback, and credential-rotation constraints.
