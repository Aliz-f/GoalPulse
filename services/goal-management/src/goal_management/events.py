"""Outbox and inbox-safe handling for the two frozen Goal Management events."""
from __future__ import annotations
from typing import Any, Protocol

class Publisher(Protocol):
    def publish_confirmed(self, routing_key:str, body:dict[str,Any]) -> None: ...
class Store(Protocol):
    def claim_outbox(self) -> list[dict[str,Any]]: ...
    def mark_delivered(self, event_id:str) -> None: ...
    def mark_retry(self, event_id:str) -> None: ...
    def inbox_seen(self, event_id:str) -> bool: ...
    def mark_inbox(self, event_id:str, event_type:str) -> None: ...
    def current_version(self, goal_id:str) -> int|None: ...
    def update_projection(self, goal_id:str, version:int, evaluation_id:str, status:str) -> None: ...

def publish_pending(store:Store,publisher:Publisher)->None:
    for event in store.claim_outbox():
        try:
            publisher.publish_confirmed('goal.activated.v1',event)
        except Exception: store.mark_retry(event['event_id'])
        else: store.mark_delivered(event['event_id'])

def consume_evaluated(store:Store,event:dict[str,Any])->str:
    if event.get('event_type')!='goal.evaluated.v1' or event.get('producer')!='goal-evaluation': raise ValueError('invalid evaluation event')
    if store.inbox_seen(event['event_id']): return 'duplicate'
    payload=event['payload']; store.mark_inbox(event['event_id'],event['event_type'])
    if store.current_version(payload['goal_id'])!=payload['goal_version']: return 'stale'
    store.update_projection(payload['goal_id'],payload['goal_version'],payload['evaluation_id'],payload['status'])
    return 'projected'
