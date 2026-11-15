# GoalPulse VPS deployment runbook

This runbook deploys the entire GoalPulse Phase 2 application with one Docker
Compose project and uses Nginx installed directly on the VPS as the TLS reverse
proxy. Nginx is deliberately not part of the Compose stack.

## 1. Release boundary

This procedure produces a controlled `demo` deployment. It does not turn the
current demo-session authentication into a public production identity system.
The supported runtime profiles are `test`, `dev`, and `demo`; there is no
approved `prod` profile in ADR-0003.

Do not advertise the deployment as a general public service until a separate
review approves production identity, account recovery, privacy/retention,
abuse controls, monitoring/alerting, incident response, vulnerability handling,
and tested backup recovery.

The live demo must use:

- `NEXT_PUBLIC_API_MODE=gateway`;
- Anthropic through Goal Intelligence, never from the browser;
- Context.dev through Discovery Monitoring, with at least one real provider
  access-policy ID beginning with `pol_`;
- real Data & Evidence mode with fixtures disabled;
- exactly two server-configured demo users;
- no purchase, payment, booking, or other financial execution.

If Context.dev has not provisioned an access-policy ID for the account, finish
the infrastructure deployment in `dev` mode, but do not claim or run the live
demo. Contact Context.dev support or the account representative for the missing
policy. Never invent an ID or remove the startup requirement.

## 2. Target topology

```text
Internet
   |
   | TCP 80/443
   v
Host Nginx + Let's Encrypt
   |-- page/assets ----------> 127.0.0.1:3000  frontend container
   `-- /v1,/v2,/health -----> 127.0.0.1:8000  Gateway container
                                                      |
                                             private app network
                                                      |
                  seven service APIs, migrations, and workers
                                                      |
                                             private data network
                                                      |
                                           PostgreSQL + RabbitMQ
```

PostgreSQL, AMQP, RabbitMQ management, frontend, and Gateway are published only
on `127.0.0.1`. The firewall must expose only SSH and Nginx. Private service APIs
have no host port.

## 3. Prerequisites

Prepare:

- a supported 64-bit Linux VPS; Ubuntu 22.04 or 24.04 LTS is the documented
  baseline;
- a DNS A/AAAA record such as `goalpulse.example.com` resolving to the VPS;
- inbound TCP 80 and 443, plus an intentionally restricted SSH port;
- Docker Engine and the Docker Compose v2 plugin from Docker's official
  repository;
- host packages `git`, `nginx`, `certbot`, and `python3-certbot-nginx` or the
  Certbot installation method recommended for the chosen OS;
- the repository release commit/tag;
- Anthropic and Context.dev API keys;
- a real Context.dev `pol_...` access-policy ID;
- an approved comma-separated merchant hostname allowlist.

A practical starting size is 2 vCPU, 4 GB RAM, and enough encrypted disk for
container images, seven databases, broker state, logs, and backup staging.
Measure the actual demo before setting capacity or availability commitments.

Verify the tools after installation:

```bash
docker version
docker compose version
nginx -v
certbot --version
```

Use Docker's current official Ubuntu installation instructions rather than an
unreviewed convenience script. Membership in the `docker` group is effectively
root access; grant it only to the deployment operator.

Authoritative installation/operations references:

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose plugin on Linux](https://docs.docker.com/compose/install/linux/)
- [Docker Compose production guidance](https://docs.docker.com/compose/how-tos/production/)
- [Nginx reverse-proxy guide](https://nginx.org/en/docs/beginners_guide.html#proxy)
- [Certbot installation guidance](https://eff-certbot.readthedocs.io/en/stable/install.html)

## 4. Create the deployment account and checkout

The following example uses `/opt/goalpulse` and a dedicated `goalpulse` account.
Adapt the repository URL and release ref.

```bash
sudo adduser --disabled-password --gecos "" goalpulse
sudo usermod -aG docker goalpulse
sudo install -d -o goalpulse -g goalpulse -m 0750 /opt/goalpulse
sudo -u goalpulse git clone <REPOSITORY_URL> /opt/goalpulse
sudo -u goalpulse git -C /opt/goalpulse checkout <RELEASE_TAG_OR_COMMIT>
```

Log out and back in after changing group membership. Confirm that the checkout
is the intended immutable release:

```bash
cd /opt/goalpulse
git status --short
git rev-parse HEAD
```

The worktree should be clean. Do not deploy an unreviewed branch or copy a local
`.env` through Git.

## 5. Configure the centralized environment

`.env.example` documents every Compose variable with its description and
accepted options. `infrastructure/release/manage_env.py` is the supported way to
create, inspect, and update the root `.env`; operators should not maintain a
second service-specific env file.

### 5.1 First-time setup with the manager

On a fresh VPS, generate all application-owned random secrets and UUIDs first,
then answer the remaining prompts:

```bash
cd /opt/goalpulse
python3 infrastructure/release/manage_env.py --generate-random
```

The manager generates only invalid or placeholder database, broker, JWT,
session, and UUID values. It does not replace an already-valid value. It then
asks one variable at a time for remaining required, invalid, or placeholder
values. API keys and other secret input are read without echo; existing secret
values are shown only as `[configured secret]`.

Prompt controls are:

- type a value and press Enter to replace the current value;
- press Enter without a value to retain the current value;
- enter `s` to skip the variable and finish it on a later run;
- enter `-` to clear the value;
- enter `g` when offered to generate a secret, UUID, or salted password hash.

The manager writes atomically and forces `.env` to mode `0600`. It also
preserves legacy/custom variables already present in `.env`, placing them in a
clearly marked section after the current template variables.

Manager exit statuses have operational meaning:

| Status | Meaning | Operator action |
|---|---|---|
| `0` | The managed environment and full Compose requirements are valid. | Continue to quiet Compose validation. |
| `1` | The template, selection, permissions, or environment is invalid in check mode. | Correct the named problem; values are not printed. |
| `2` | `.env` was safely written but skipped or invalid variables remain. | Rerun the manager and finish the named fields. |
| `130` | Input was cancelled with Ctrl-C/EOF before writing. | Rerun; no changes from that invocation were saved. |

If a value is unavailable, enter `s`. The partial file is intentionally saved
so setup can resume later:

```bash
python3 infrastructure/release/manage_env.py
```

Do not start or claim the live demo while `--check` returns nonzero. In
particular, a skipped Context.dev policy ID cannot be replaced with a fabricated
value or a fixture fallback.

Common management commands are:

```bash
# Review every variable; secrets remain hidden.
python3 infrastructure/release/manage_env.py --all

# Change one value even when its current value is valid.
python3 infrastructure/release/manage_env.py --only GI_LLM_MODEL

# Change matching public origins together. --only is repeatable.
python3 infrastructure/release/manage_env.py \
  --only NEXT_PUBLIC_GATEWAY_URL \
  --only GATEWAY_DEMO_ALLOWED_ORIGIN

# Redacted, non-interactive readiness check; never writes .env.
python3 infrastructure/release/manage_env.py --check

# Manage an alternate staging file when explicitly required.
python3 infrastructure/release/manage_env.py --env-file /secure/path/staging.env
```

Do not paste `.env` contents into tickets, chat, shell history, CI artifacts, or
logs. Do not use `--env-file` to create competing per-service configuration in
the release deployment; the root `/opt/goalpulse/.env` remains authoritative.

### 5.2 Deployment and public origin

Set:

```dotenv
COMPOSE_PROJECT_NAME=goalpulse
GOALPULSE_PROFILE=demo
FRONTEND_HOST_PORT=3000
GATEWAY_HOST_PORT=8000
POSTGRES_HOST_PORT=5432
RABBITMQ_HOST_PORT=5672
RABBITMQ_MANAGEMENT_HOST_PORT=15672
NEXT_PUBLIC_API_MODE=gateway
NEXT_PUBLIC_GATEWAY_URL=https://goalpulse.example.com
GATEWAY_DEMO_ALLOWED_ORIGIN=https://goalpulse.example.com
```

Replace the example hostname everywhere. The two HTTPS origins must match
exactly and must not contain a path. `NEXT_PUBLIC_*` values are compiled into the
browser bundle and are not secrets. Changing them requires rebuilding frontend.

All host port variables remain loopback-bound by Compose. If the VPS already
uses a default port, change the host-side number in `.env` and make the matching
Nginx upstream change. Do not change container ports.

### 5.3 Generated and externally supplied values

Generate a fresh URL-safe value for every password and JWT/session secret. At
each matching manager prompt, enter `g`, or use `--generate-random` on the
first run. The manager generates independent values without displaying them.

| Variable class | How to configure |
|---|---|
| `POSTGRES_PASSWORD`, all `*_DB_PASSWORD` values, `RABBITMQ_DEFAULT_PASS`, JWT secrets, and the session secret | Enter `g`, or use `--generate-random`. |
| Demo user UUIDs | Enter `g`, or use `--generate-random`; the manager enforces distinct UUID v4 values. |
| Demo user password hashes | Enter `g`, then type and confirm the intended login password privately. Only the salted hash is stored. |
| Anthropic and Context.dev API keys | Paste provider-issued credentials at the hidden prompt. The manager cannot generate them. |
| Context.dev policy IDs | Enter one or more provider-issued `pol_...` IDs. Never invent an ID. |
| Public origins, merchant hosts, and model ID | Enter deployment/account-specific approved values. |

For a manual recovery workflow, run the following command separately for each
field and paste only into `.env`:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Use independent values for:

- `POSTGRES_PASSWORD` and every `*_DB_PASSWORD`;
- `RABBITMQ_DEFAULT_PASS`;
- `GATEWAY_DEV_JWT_SECRET`;
- `SERVICE_JWT_SECRET`;
- `GATEWAY_DEMO_SESSION_SECRET`.

Do not reuse the API keys as application secrets. URL-safe values avoid
breaking the PostgreSQL and AMQP URLs assembled by Compose.

The manager also generates two distinct user UUIDs when you enter `g`. The
manual equivalent is:

```bash
python3 -c 'import uuid; print(uuid.uuid4())'
```

For each demo-user password hash, enter `g` and provide the password twice. The
manager reads plaintext without echo and stores only the generated hash. The
manual equivalent is:

```bash
PYTHONPATH=infrastructure/gateway python3 -c \
  'from getpass import getpass; from goalpulse_gateway.sessions import hash_demo_password; print(hash_demo_password(getpass()))'
```

Put only the resulting `pbkdf2_sha256$...` values in
`GATEWAY_DEMO_USER_A_PASSWORD_HASH` and
`GATEWAY_DEMO_USER_B_PASSWORD_HASH`. Use long, unique passwords and transfer
them to the two authorized users through an approved secret channel.

### 5.4 Providers and evidence

Configure the real runtime:

```dotenv
GI_LLM_PROVIDER=anthropic
GI_LLM_MODEL=<AN_APPROVED_MODEL_ID>
ANTHROPIC_API_KEY=<SECRET>
DM_PROVIDER=context_dev
CONTEXT_DEV_API_KEY=<SECRET>
CONTEXT_DEV_ACCESS_POLICY_IDS=pol_<PROVIDER_ISSUED_VALUE>
DE_EVIDENCE_MODE=real
DE_EVIDENCE_PROVIDER=real
DE_ALLOW_FIXTURE=false
DE_MERCHANT_HOSTS=merchant-one.example,merchant-two.example
```

Model availability is account- and date-dependent; select a model the Anthropic
account can use and that has been verified against Goal Intelligence's tests.
Policy IDs may be comma-separated. Merchant entries are hostnames only—no
scheme, port, path, wildcard, localhost, or private address.

### 5.5 Validate before Docker

Check permissions, placeholders, completeness, ports, origins, secrets, demo
users, provider modes, policy IDs, and merchant hosts. The validator reports
variable names and problems only; it never prints values.

```bash
cd /opt/goalpulse
python3 infrastructure/release/manage_env.py
python3 infrastructure/release/manage_env.py --check
python3 infrastructure/release/validate_env.py --env-file .env
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
```

All four commands must pass. Also verify that no placeholder remains:

```bash
grep -n 'CHANGE_ME' .env
```

The expected result is no output and exit status 1 from `grep`. Do not use
`docker compose config` without care in shared logs because its expanded output
can include secrets; `--quiet` performs validation without rendering it.

If Compose warns that random-looking variables are unset, an older `.env` has
unquoted `$` separators in a PBKDF2 demo password hash. Do not export or replace
those warning names. Update to the current release and run the manager once:

```bash
python3 infrastructure/release/manage_env.py
python3 infrastructure/release/manage_env.py --check
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
```

The manager preserves the hash value while single-quoting it for Compose. The
quiet Compose command must then produce no interpolation warnings.

## 6. Run repository release checks

Prefer running the full checks in CI for the exact release commit. If the VPS is
also the controlled validation host, install the documented Python/Node tooling
and run from the repository root:

```bash
python scripts/validate_contracts.py
python -m pytest -q
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
env NEXT_PUBLIC_GOALPULSE_PROFILE=demo NEXT_PUBLIC_API_MODE=gateway \
  NEXT_PUBLIC_GATEWAY_URL=https://goalpulse.example.com \
  npm --prefix frontend run build
```

Replace the example origin in the build check. Direct npm commands do not load
the root Compose `.env`; the actual release image build receives these public
values from Compose.

Provider credentials are not proof of a live pass. Run the recorded release lane
separately and retain its redacted report:

```bash
python infrastructure/release/run_phase2_gate.py \
  --lane recorded --output /tmp/goalpulse-recorded-release.json
```

Follow `docs/phase2/R2-D1-03-RUNBOOK.md` for the disposable durable lane and the
explicitly authorized controlled-live smoke. Never make live provider calls from
default CI.

## 7. Build and start the entire application

The release start command is:

```bash
cd /opt/goalpulse
docker compose -f infrastructure/docker-compose.yml --env-file .env \
  up -d --build --wait --wait-timeout 300
```

This one command builds frontend, Gateway, and service images; starts PostgreSQL
and RabbitMQ; imports broker topology; applies service-owned migrations; starts
all APIs and workers; and waits for frontend/Gateway dependency-aware health.

Inspect status without showing environment values:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
docker compose -f infrastructure/docker-compose.yml --env-file .env logs \
  --tail 100 gateway frontend
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
curl --fail --silent --show-error --output /dev/null http://127.0.0.1:3000/
```

All long-running services should be running and healthy. One-shot init and
migration containers should exit successfully. Never post raw provider payloads,
authorization headers, DSNs, prompts, or `.env` values when sharing logs.

## 8. Bootstrap TLS and host Nginx

Install Nginx and Certbot using the operating system's supported package path.
Ensure DNS resolves before requesting a certificate.

### 8.1 Temporary HTTP challenge site

Create `/etc/nginx/sites-available/goalpulse-bootstrap` with the actual hostname:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name goalpulse.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and test it:

```bash
sudo ln -s /etc/nginx/sites-available/goalpulse-bootstrap /etc/nginx/sites-enabled/goalpulse-bootstrap
sudo nginx -t
sudo systemctl reload nginx
```

Request the initial certificate with the webroot challenge:

```bash
sudo certbot certonly --webroot -w /var/www/html \
  -d goalpulse.example.com -m <OPERATIONS_EMAIL> --agree-tos --no-eff-email
```

### 8.2 Final reverse proxy

Install the repository template and edit every example hostname. If host ports
were changed in `.env`, edit the two upstream ports too.

```bash
sudo install -o root -g root -m 0644 \
  infrastructure/nginx/goalpulse.conf.example /etc/nginx/sites-available/goalpulse
sudoedit /etc/nginx/sites-available/goalpulse
sudo ln -s /etc/nginx/sites-available/goalpulse /etc/nginx/sites-enabled/goalpulse
sudo unlink /etc/nginx/sites-enabled/goalpulse-bootstrap
sudo nginx -t
sudo systemctl reload nginx
```

The final configuration sends `/v1`, `/v2`, and `/health` to Gateway and all
other paths to Next.js. It passes the original host, client chain, and scheme;
supports connection upgrades; limits request bodies; and leaves backend ports on
loopback.

Test renewal after the final configuration is active:

```bash
sudo certbot renew --dry-run
systemctl list-timers | grep -i certbot
```

After HTTPS is confirmed stable, an operations/security review may add HSTS.
Adding it before the domain and renewal path are proven can make recovery harder.

## 9. Firewall and exposure checks

When using UFW, allow SSH before enabling it:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status verbose
```

Confirm that only SSH, 80, and 443 are public and that GoalPulse ports listen on
loopback:

```bash
sudo ss -lntp
```

Do not add firewall rules for 3000, 8000, 5432, 5672, or 15672. To inspect
RabbitMQ management remotely, use an authenticated SSH tunnel rather than
publishing it to the Internet.

## 10. External smoke verification

From a separate machine:

```bash
curl --fail --silent --show-error https://goalpulse.example.com/health/ready
curl --fail --silent --show-error --output /dev/null https://goalpulse.example.com/
```

Then complete the browser smoke from `R2-D1-03-RUNBOOK.md`:

1. sign in as demo user A over HTTPS;
2. submit only the approved synthetic, non-sensitive goal;
3. review and confirm the exact draft revision;
4. observe activation, real discovery, source checking, evaluation, and the UI;
5. verify missing evidence remains Candidate/Unknown;
6. confirm duplicate event delivery does not create duplicate business results;
7. confirm logout/session expiry and user B isolation;
8. save only redacted status, latency, correlation/request IDs, and timestamps.

Do not call or simulate checkout, payment, booking, or order endpoints. A provider
failure is a visible failure, not permission to switch to fixtures.

## 11. Routine operations

Use one consistent project name and env file for every command:

```bash
cd /opt/goalpulse
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
docker compose -f infrastructure/docker-compose.yml --env-file .env logs --tail 200
docker compose -f infrastructure/docker-compose.yml --env-file .env restart <SERVICE>
```

Check host disk, memory, certificate expiry, container restart counts, health,
database growth, broker queue depth, and backup age. Configure an external uptime
check against HTTPS readiness and an alert destination that does not depend on
GoalPulse itself.

The default Docker log driver can grow without a host policy. Configure and test
host-level Docker log rotation before sustained use. Logs must exclude secrets,
authorization values, prompts, raw provider bodies, and unnecessary personal
data.

## 12. Backups and recovery

Create a root-readable backup directory on encrypted storage with off-host
replication and retention appropriate to the demo data policy:

```bash
sudo install -d -o root -g root -m 0700 /var/backups/goalpulse
```

Run a logical backup without stopping the stack:

```bash
sudo -i
cd /opt/goalpulse
umask 077
docker compose -f infrastructure/docker-compose.yml --env-file .env \
  exec -T postgres sh -c 'pg_dumpall --clean --if-exists --username="$POSTGRES_USER"' \
  | gzip -9 > "/var/backups/goalpulse/postgres-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
exit
```

Protect backups as sensitive data. A backup is not proven until a scheduled
restore drill succeeds in an isolated environment and application invariants are
checked. RabbitMQ contains transient workflow state; durable application truth
and inbox/outbox state are in service databases, but a recovery plan must still
account for in-flight events and safe replay.

Do not test restoration against the live volumes. Restore into isolated names,
validate contracts/migrations, and run the end-to-end smoke before accepting the
recovery procedure.

## 13. Upgrade procedure

For every release:

1. record the running Git commit and image status;
2. confirm a recent off-host backup and recovery drill status;
3. fetch and review the intended signed/approved release;
4. run contract, test, env, and Compose validation;
5. review migration compatibility and release notes;
6. build and start with dependency-aware waiting;
7. run HTTPS and browser smoke checks;
8. retain redacted evidence and monitor after rollout.

Example application step after checkout:

```bash
cd /opt/goalpulse
python3 infrastructure/release/manage_env.py
python3 infrastructure/release/manage_env.py --check
python3 infrastructure/release/validate_env.py --env-file .env
docker compose -f infrastructure/docker-compose.yml --env-file .env config --quiet
docker compose -f infrastructure/docker-compose.yml --env-file .env \
  up -d --build --wait --wait-timeout 300
```

Compose recreates changed services and leaves named database/broker volumes in
place. Never use `down -v` during an upgrade.

## 14. Rollback and failure handling

If the new application code fails and migrations are backward compatible,
checkout the previously recorded release and rerun the same build/start command.
If a migration is not backward compatible, do not guess or manually edit service
tables. Follow the reviewed migration-specific recovery plan and restore only
from a verified backup into an isolated environment first.

Useful diagnostics:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
docker compose -f infrastructure/docker-compose.yml --env-file .env logs \
  --since 15m --tail 300 gateway frontend
sudo nginx -t
sudo journalctl -u nginx --since '15 minutes ago'
```

Stop the application without deleting data only when necessary:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env stop
```

Do not delete volumes, drop databases, rotate stored database/broker credentials,
or force-reset Git during incident response without an explicit reviewed plan.

## 15. Credential rotation note

Changing JWT/session/provider values in `.env` and recreating affected containers
rotates stateless application credentials; existing demo sessions become invalid
when the session secret changes. Rotate provider keys in their provider consoles
and update `.env` through a secure channel.

Use the manager for a deliberate stateless secret update, entering `g` when
generation is offered or pasting the newly issued provider key at the hidden
prompt:

```bash
python3 infrastructure/release/manage_env.py --only SERVICE_JWT_SECRET
python3 infrastructure/release/manage_env.py --only GATEWAY_DEMO_SESSION_SECRET
python3 infrastructure/release/manage_env.py --only ANTHROPIC_API_KEY
python3 infrastructure/release/manage_env.py --check
```

Do not use `--generate-random` as a general rotation command: it preserves valid
values by design. Use `--only NAME` for an intentional replacement. After a
stateless change, recreate only the documented dependent services and verify
readiness and authentication behavior.

PostgreSQL and RabbitMQ bootstrap variables are different: their images consume
those values only when initializing a new volume. Editing `.env` alone does not
change users stored in existing volumes and can lock services out. Database and
broker rotation therefore requires a separately reviewed, service-aware runbook
that changes the stored credential and `.env` together, verifies every dependent
container, and retains a recovery path.

## 16. Release checklist

- [ ] Approved release commit/tag is checked out and worktree is clean.
- [ ] The environment manager `--check` passes without displaying values.
- [ ] `.env` is mode `0600`, contains no placeholders, and is not tracked.
- [ ] Central environment validator and quiet Compose validation pass.
- [ ] Contract, Python, frontend, recorded, and durable gates pass.
- [ ] Context.dev key and real `pol_...` policy ID are configured.
- [ ] Anthropic model/key are verified for the deployment account.
- [ ] Evidence is real, fixtures are disabled, merchant hosts are approved.
- [ ] Full Compose stack starts with one command and becomes healthy.
- [ ] Nginx config test, HTTPS, and Certbot renewal dry-run pass.
- [ ] Only SSH/80/443 are publicly reachable.
- [ ] Controlled-live browser smoke passes with redacted evidence.
- [ ] Backup is current, off-host, protected, and restore-tested.
- [ ] Monitoring, log rotation, alerting, and operator contacts are active.
- [ ] Public-production limitations are disclosed and accepted.
