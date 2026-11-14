#!/usr/bin/env python3
"""Validate all JSON Schema definitions and all bundled examples.

Run from any CWD: python scripts/validate_contracts.py
"""
from __future__ import annotations
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / 'contracts' / 'schemas'
EXAMPLES = ROOT / 'contracts' / 'examples'

def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))

def validate_contracts() -> None:
    definitions = {p.name: load(p) for p in SCHEMAS.glob('*.json')}
    for name, schema in definitions.items():
        Draft202012Validator.check_schema(schema)
    mapping = {
        'goal-spec.': 'goal-spec.v1.schema.json',
        'goal-draft.': 'goal-draft.v1.schema.json',
        'observation.': 'observation.v1.schema.json',
        'goal-evaluation.': 'goal-evaluation.v1.schema.json',
        'event.': 'domain-event.v1.schema.json',
    }
    for p in sorted(EXAMPLES.glob('*.json')):
        key = next((v for prefix, v in mapping.items() if p.name.startswith(prefix)), None)
        if key is None:
            raise AssertionError(f'No schema assigned to {p.name}')
        validator = Draft202012Validator(definitions[key], format_checker=FormatChecker())
        errors = list(validator.iter_errors(load(p)))
        if errors:
            raise AssertionError(f'{p.name}: {errors[0].message} at {list(errors[0].path)}')
        print(f'PASS {p.name}')
    spec = yaml.safe_load((ROOT/'contracts/openapi/gateway.v1.yaml').read_text())
    assert spec['openapi'].startswith('3.1.')
    for path, operations in spec['paths'].items():
        assert path.startswith('/v1/'), path
        assert any(x in operations for x in ('get', 'post', 'patch')), path
    proof_spec = yaml.safe_load((ROOT/'contracts/openapi/internal.evidence-proof.v1.yaml').read_text())
    assert proof_spec['openapi'].startswith('3.1.')
    assert set(proof_spec['paths']) == {
        '/internal/v2/extractions/with-proof',
        '/internal/v2/evidence-checks/{observation_id}',
    }
    route = yaml.safe_load((ROOT/'contracts/event-routing.yaml').read_text())
    assert set(route['routing']) == set(definitions['domain-event.v1.schema.json']['properties']['event_type']['enum'])
    assert route['exchange_type'] == 'topic'
    for event_name, info in route['routing'].items():
        assert info['producer'] in definitions['domain-event.v1.schema.json']['properties']['producer']['enum']
        assert info['consumers'], event_name
    print(f'PASS event routing for {len(route["routing"])} event types')
    print(f'PASS OpenAPI YAML with {len(spec["paths"])} paths')
    print(f'PASS evidence-proof OpenAPI with {len(proof_spec["paths"])} paths')
    print(f'PASS {len(definitions)} JSON Schemas + {len(list(EXAMPLES.glob("*.json")))} examples')

if __name__ == '__main__':
    validate_contracts()
