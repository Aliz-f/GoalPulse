"""Pure evaluator dispatch against the authoritative JSON schemas."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, FormatChecker

Document = dict[str, Any]


class Contracts:
    """Load explicit local contract artifacts, never remote schema references."""

    def __init__(self, schema_directory: Path):
        self.validators = {
            name: Draft202012Validator(
                json.loads((schema_directory / f"{name}.v1.schema.json").read_text()),
                format_checker=FormatChecker(),
            )
            for name in ("goal-spec", "observation", "goal-evaluation", "domain-event")
        }

    def validate(self, name: str, value: Document) -> None:
        """Raise jsonschema.ValidationError for an invalid boundary document."""
        self.validators[name].validate(value)


class GoalEvaluator(Protocol):
    """Implementations must be deterministic and perform no network or DB I/O."""

    def evaluate(
        self, goal: Document, observations: Sequence[Document], now_utc: datetime
    ) -> Document: ...


class Dispatcher:
    """Validate inputs/output and refuse unsupported or unimplemented domains."""

    def __init__(self, contracts: Contracts, evaluators: Mapping[str, GoalEvaluator]):
        if set(evaluators) - {"product_purchase", "recurring_procurement"}:
            raise ValueError("Unsupported evaluator registration")
        self.contracts = contracts
        self.evaluators = dict(evaluators)

    def evaluate(
        self, goal: Document, observations: Sequence[Document], now_utc: datetime
    ) -> Document:
        if now_utc.utcoffset() is None or now_utc.utcoffset().total_seconds() != 0:
            raise ValueError("Evaluation clock must be timezone-aware UTC")
        self.contracts.validate("goal-spec", goal)
        for observation in observations:
            self.contracts.validate("observation", observation)
        evaluator = self.evaluators.get(goal["goal_type"])
        if evaluator is None:
            raise NotImplementedError(goal["goal_type"])
        result = evaluator.evaluate(goal, observations, now_utc)
        self.contracts.validate("goal-evaluation", result)
        if (result["goal_id"], result["goal_version"]) != (goal["goal_id"], goal["version"]):
            raise ValueError("Evaluation references a different goal version")
        supplied = {o["observation_id"] for o in observations}
        referenced = set(result["candidate_observation_ids"])
        for condition in result["conditions"]:
            referenced.update(condition["observation_ids"])
        if not referenced <= supplied:
            raise ValueError("Evaluation cites evidence that was not supplied")
        return result
