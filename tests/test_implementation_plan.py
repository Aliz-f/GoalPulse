from pathlib import Path
import re
import yaml

ROOT=Path(__file__).resolve().parents[1]


def load_board():
    return yaml.safe_load((ROOT/'tasks/task-index.yaml').read_text(encoding='utf-8'))


def test_all_seven_service_specs_exist_and_have_acceptance_tests():
    names=['goal-management','goal-intelligence','discovery-monitoring','data-evidence','goal-evaluation','notification','agent-integration']
    for i, name in enumerate(names,1):
        spec=ROOT/'docs/implementation'/f'{i:02d}-{name}.md'
        assert spec.exists()
        content=spec.read_text(encoding='utf-8')
        assert len(content)>2400, f'Under-specified {name}'
        assert 'Acceptance tests' in content


def test_task_index_44_tasks_four_owners_unique_ids():
    tasks=load_board()['tasks']
    assert len(tasks)==44
    assert len({t['id'] for t in tasks})==len(tasks)
    assert {t['owner'] for t in tasks}=={1,2,3,4}
    assert {t['priority'] for t in tasks} <= {'P0','P1','P2'}
    for t in tasks:
        assert t['status']=='todo'
        assert t['acceptance'] and t['allowed_paths'] and t['implementation']
        assert (ROOT/t['source']).exists()


def test_all_dependencies_exist_and_acyclic():
    tasks=load_board()['tasks']
    refs={t['id']:set(t['dependencies']) for t in tasks}
    for task_id,deps in refs.items():
        assert not deps-{*refs}, (task_id,deps-{*refs})
        assert task_id not in deps
    seen=set()
    visiting=set()
    def visit(node):
        assert node not in visiting, f'Cycle involving {node}'
        if node in seen:
            return
        visiting.add(node)
        for d in refs[node]:
            visit(d)
        visiting.remove(node)
        seen.add(node)
    for x in refs:
        visit(x)


def test_internal_proposal_is_valid_openapi_draft():
    p=ROOT/'contracts/openapi/internal.proposed.v1.yaml'
    d=yaml.safe_load(p.read_text(encoding='utf-8'))
    assert d['openapi']=='3.1.0'
    assert 'proposed' in d['info']['version']
    assert len(d['paths'])>=10
    methods={'get','post','put','patch','delete'}
    assert all(any(k in obj for k in methods) for obj in d['paths'].values())
    for path, operations in d['paths'].items():
        assert path.startswith('/internal/v1/')
        for m, spec in operations.items():
            assert m in methods
            assert spec.get('operationId'),path
            assert spec.get('responses'),path


def test_public_extension_proposal_not_replacing_frozen_gateway():
    draft=yaml.safe_load((ROOT/'contracts/openapi/gateway.extensions.proposed.v1.yaml').read_text())
    current=yaml.safe_load((ROOT/'contracts/openapi/gateway.v1.yaml').read_text())
    assert 'proposed' in draft['info']['version']
    assert all(p.startswith('/v1/') for p in draft['paths'])
    assert not set(draft['paths']) & set(current['paths'])


def test_service_readmes_link_to_correct_specs_and_agent_rules():
    for i, name in enumerate(['goal-management','goal-intelligence','discovery-monitoring','data-evidence','goal-evaluation','notification','agent-integration'],1):
        t=(ROOT/'services'/name/'README.md').read_text()
        assert f'{i:02d}-{name}.md' in t
        assert 'AGENTS.md' in t


def test_policy_marks_proposed_contracts_unapproved():
    s=(ROOT/'AGENTS.md').read_text()
    assert 'internal.proposed.v1.yaml' in s
    assert 'not an approved contract' in s
