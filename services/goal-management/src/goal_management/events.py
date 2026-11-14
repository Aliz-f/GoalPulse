"""Outbox and inbox-safe handling for the two frozen Goal Management events."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker


_EVENT_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "schemas" / "domain-event.v1.schema.json"
_EVENT_VALIDATOR = Draft202012Validator(
    json.loads(_EVENT_SCHEMA_PATH.read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)

class Publisher(Protocol):
    def publish_confirmed(self, routing_key:str, body:dict[str,Any]) -> None: ...
class Store(Protocol):
    def claim_outbox(self) -> list[dict[str,Any]]: ...
    def mark_delivered(self, event_id:str) -> None: ...
    def mark_retry(self, event_id:str) -> None: ...
    def process_evaluated(
        self,
        *,
        event_id: str,
        event_type: str,
        goal_id: str,
        goal_version: int,
        evaluation_id: str,
        status: str,
    ) -> str: ...

def publish_pending(store:Store,publisher:Publisher)->None:
    for event in store.claim_outbox():
        try:
            publisher.publish_confirmed('goal.activated.v1',event)
        except Exception: store.mark_retry(event['event_id'])
        else: store.mark_delivered(event['event_id'])

def consume_evaluated(store:Store,event:dict[str,Any])->str:
    validation_errors = sorted(_EVENT_VALIDATOR.iter_errors(event), key=lambda error: list(error.path))
    if validation_errors:
        raise ValueError(f"invalid domain event: {validation_errors[0].message}")
    if event.get('event_type')!='goal.evaluated.v1' or event.get('producer')!='goal-evaluation': raise ValueError('invalid evaluation event')
    payload=event['payload']
    return store.process_evaluated(
        event_id=event['event_id'],
        event_type=event['event_type'],
        goal_id=payload['goal_id'],
        goal_version=payload['goal_version'],
        evaluation_id=payload['evaluation_id'],
        status=payload['status'],
    )
