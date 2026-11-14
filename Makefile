.PHONY: up down ps logs validate test
DC = docker compose -f infrastructure/docker-compose.yml --env-file .env
up:
	$(DC) up -d
ps:
	$(DC) ps
logs:
	$(DC) logs --tail=100
down:
	$(DC) down
validate:
	python scripts/validate_contracts.py
test:
	python -m pytest -q
