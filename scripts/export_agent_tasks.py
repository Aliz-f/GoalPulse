"""Export one self-contained AGENTS.md-aware handoff per task from task-index.yaml."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
board=yaml.safe_load((ROOT/'tasks/task-index.yaml').read_text(encoding='utf-8'))
out=ROOT/'tasks/issues'
out.mkdir(parents=True,exist_ok=True)
for task in board['tasks']:
    deps=', '.join(task['dependencies']) or 'None (check prerequisites in owner task file)'
    t=f'''# {task['id']} — {task['title']}

**Owner:** Developer {task['owner']}  
**Priority:** {task['priority']}  
**Status:** TODO  
**Dependencies:** {deps}

## Mandatory preflight

1. Read `AGENTS.md`, `docs/implementation/README.md` and the exact owner task section in `{task['source']}`.
2. Read your relevant service specification in `docs/implementation/0N-*.md` and the specific frozen schemas/events/OpenAPI touched by the task.
3. Verify any relevant `contracts/openapi/*.proposed*.yaml` has an accepted ICP/ADR. A proposal is **not** implementation authority.
4. Check `git status` and another developer's work before editing.

## Objective and concrete work

{task['implementation']}

## Allowed write scope

{task['allowed_paths']}

No changes to other services, existing `contracts/*.v1`, event routing, shared infrastructure or root dependencies unless a separately approved coordinated PR explicitly permits them.

## Acceptance criteria

{task['acceptance']}

## Required tests and handoff

```bash
python scripts/validate_contracts.py
python -m pytest -q
```

Add task-specific unit/integration tests; test failure paths, idempotency, ownership and stale data where applicable. If Docker or real Context.dev wasn't available, say **NOT RUN** rather than claiming passing tests.

At the end, report changed files, test output, event/API/schema impacts, migrations and new env vars, remaining risks and exactly which downstream task can now proceed.
'''
    (out/f"{task['id']}.md").write_text(t,encoding='utf-8')
(out/'README.md').write_text('# Agent-ready individual task briefs\n\nEach `.md` file maps 1:1 to a task in `tasks/task-index.yaml` and the owner task docs. Open one file per Codex/Claude Code assignment. Check all upstream dependencies before starting; never hand an entire developer backlog to one agent in a single unreviewed prompt.\n',encoding='utf-8')
print('Exported',len(board['tasks']),'task briefs to',out)
