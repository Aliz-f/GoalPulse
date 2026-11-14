# Phase 1 — exact runbook (3 hours suggested)

| When | Owner | Action | Evidence of completion |
|---|---|---|---|
| 00:00–00:20 | Dev 1 only | Create GitHub repo, protect main, add monorepo folders; invite all four | Others can clone; `main` protected |
| 00:20–01:00 | Dev 1 drafts, all review | Agree `GoalSpec`, Draft, Observation, Evaluation, event routing and API names | Schemas validate; four approvals in issue/PR |
| 00:20–01:00 | Dev 3 in parallel | Obtain Context.dev account/key **privately**; run a scratch scrape on two public product pages | Actual samples saved locally without keys; note missing data, cache age |
| 00:20–01:00 | Dev 2 in parallel | Draft user journeys: missing fields, confirmation, list/individual goal page | Flow accepted by team |
| 00:20–01:00 | Dev 4 in parallel | Define six evaluator fixtures covering satisfied / unsatisfied / unknown / stale / wrong model / missing shipping | Fixture expectations agreed |
| 01:00–01:40 | Dev 1 | Install/verify Docker Compose, PostgreSQL, RabbitMQ; copy `.env.example` to `.env` | `docker compose ... ps` reports healthy |
| 01:00–01:40 | Dev 2 | Translate conversation examples into draft responses and candidate goal examples | Valid `GoalDraft` and `GoalSpec` examples |
| 01:00–01:40 | Dev 3 | Confirm achievable extraction scope; decide which source field maps to normalized Observation | Extraction mapping spec completed |
| 01:00–01:40 | Dev 4 | Write acceptance tests against agreed schemas | Tests validate valid examples and reject invalid |
| 01:40–02:20 | Dev 1 + Dev 4 | Run local schema tests and CI; resolve disagreements | `python scripts/validate_contracts.py` and pytest succeed |
| 02:20–03:00 | Whole team | Contract freeze, owner assignments, branch protection and integration plan | Version 1 tags + sign-off; Phase 2 unblocked |

## Developer 1 commands

```bash
# In this starter-kit root:
cp .env.example .env
# EDIT .env development passwords with a text editor.
python -m pip install -r requirements-dev.txt
python scripts/validate_contracts.py
python -m pytest -q
# If Docker Engine is available:
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

**Do not** use `docker compose down -v` unless you deliberately want to erase local database data. Init SQL executes only when PostgreSQL volume is first created.

## Exit criteria / sign-off

1. Both sample confirmed goals validate against frozen schema.
2. Incomplete draft validates; incomplete spec is REJECTED.
3. `Observation` with null shipping + unknown stock validates (uncertainty preserved).
4. Satisfied, unsatisfied, and unknown evaluator examples validate.
5. All four event examples validate and match routing keys.
6. CI reports `PASS` for tests; compose services are healthy on one team machine (can't verify in the supplied offline environment).
7. Dev 3 has verified at least one relevant real data source with Context.dev using a private API key.
8. Team accepts: no code from LLM is authoritative without server validation, no monitoring until confirmation, no auto-purchase.

**Go/no-go:** Do not spend the next phase building a false live-price demo if Context.dev cannot expose the required product price/availability for chosen merchants. Pick supported sources or a narrower goal.
