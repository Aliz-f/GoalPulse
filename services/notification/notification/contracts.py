"""Validate local frozen artifacts; never import another service's private code."""
import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

Document = dict[str, Any]


class Contracts:
    def __init__(self, schema_directory: Path):
        self.validators = {name: Draft202012Validator(
            json.loads((schema_directory / f'{name}.v1.schema.json').read_text()),
            format_checker=FormatChecker())
            for name in ('domain-event', 'goal-evaluation', 'goal-spec', 'observation')}

    def validate(self, name: str, value: Document) -> None:
        self.validators[name].validate(value)
