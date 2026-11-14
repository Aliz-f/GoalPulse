# Team onboarding — Codex, Claude Code, and four parallel developers

## 1. How instructions are loaded

- Start Codex from the Git repository root or a subdirectory. Codex loads root `AGENTS.md` and applicable nested rules.
- Start Claude Code from the repository root. Root `CLAUDE.md` imports `@AGENTS.md` so that the project uses one maintained team policy.
- After editing these files, restart an agent session if it does not pick up the new instructions. Ask it to summarize the effective rules and its allowed change paths before assigning code tasks.
- Avoid creating service-local `AGENTS.md` or `CLAUDE.md` unless the team explicitly approves extra **non-conflicting** instructions.

## 2. Human repository controls — mandatory for real enforcement

The policy file does **not** mechanically enforce approval. Set these in GitHub/GitLab:

1. Protect `main`; require pull requests/merge requests, disallow force-push and direct pushes.
2. Require passing contract tests and relevant service CI before merge.
3. Require at least one reviewer for each service PR; for `contracts/`, `infrastructure/`, `.github/`, or ADRs require Developer 1 and impacted service owners.
4. Configure CODEOWNERS once **real GitHub usernames/team handles** are available. Do not commit dummy placeholders that appear to enforce ownership.
5. Assign one explicit owner and allowed file paths for every issue using `docs/agent-task-template.md`.
6. Prefer one branch/worktree per agent; do not share a mutable working tree across four agents.
7. Make all changes to contract schemas through an approved Interface Change Proposal and ADR with producer/consumer tests.

## 3. First-minute agent prompt

> Read `AGENTS.md` and the repository contracts before coding. You are Developer N, owning `<service>`. Your allowed write paths are `<paths>`. Summarize the relevant schema fields, event names, and tests you will run. Do not change shared contracts or other services without an approved Interface Change Proposal. Then implement `<task>` with unit tests and return the required completion report.

## 4. Local verification

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_contracts.py
python -m pytest -q

# With local Docker installed and a private .env prepared:
docker compose -f infrastructure/docker-compose.yml --env-file .env config
docker compose -f infrastructure/docker-compose.yml --env-file .env up -d
docker compose -f infrastructure/docker-compose.yml --env-file .env ps
```

If not executed, explicitly record `NOT RUN` and why. The starter has contracts and infrastructure only; business services remain Phase 2 work.
