#!/usr/bin/env python3
"""Read-only consistency checks for merged GoalPulse Phase 2 artifacts."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def main():
    plan = json.loads((ROOT / 'tasks/phase2/tasks.json').read_text(encoding='utf-8'))
    tasks = plan['tasks']
    by_id = {task['id']: task for task in tasks}
    assert len(tasks) == len(by_id) == 15
    assert set(t['owner'] for t in tasks) == {'D1','D2','D3','D4'}
    for item in tasks:
        assert set(item['deps']).issubset(by_id)
        assert item['accept'] and item['work'] and item['paths']
        assert (ROOT / f'tasks/phase2/issues/{item["id"]}.md').is_file()
        owner_doc = ROOT / f'tasks/phase2/developer-{item["owner"][-1]}.md'
        assert owner_doc.is_file() and item['id'] in owner_doc.read_text(encoding='utf-8')
    seen = set()
    while len(seen) != len(tasks):
        ready = [t for t in tasks if t['id'] not in seen and set(t['deps']) <= seen]
        assert ready, 'Cyclic task dependency found'
        seen.update(t['id'] for t in ready)
    for required in [
        'README-START-HERE-FA.md', 'docs/adr/0003-phase2-mvp-decisions.md',
        'docs/phase2/ARCHITECTURE.md', 'docs/phase2/EXECUTION-GATES.md',
        'docs/phase2/RELEASE-TESTS.md', 'docs/phase2/RUNTIME-ENV.md',
        'docs/phase2/CONTRACT-CHANGE-PLAN.md',
        'prompts/START-PHASE2-CODEX.md', 'prompts/D1-APPLY-DECISIONS-FIRST.md',
        'contracts/phase2/gateway.phase2.extension.DECIDED-NOT-MERGED.yaml',
        'contracts/openapi/gateway.phase2.extension.v1.yaml'
    ]:
        assert (ROOT / required).is_file(), required
    assert not (ROOT / 'contracts/phase2/schemas').exists(), 'Do not add shadow frozen v1 schemas under phase2'
    print('PASS: 15 task cards, 4 developers, acyclic dependencies, required docs present.')
    print('NOTE: No implementation, external provider, Docker or live end-to-end tests run.')

if __name__ == '__main__':
    main()
