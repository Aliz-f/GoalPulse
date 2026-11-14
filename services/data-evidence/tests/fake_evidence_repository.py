"""Transactional test double only; PostgreSQL is the sole production backend."""

from contextlib import contextmanager
from copy import deepcopy


class MemoryRepository:
    def __init__(self, fail_at=None):
        self.state = {"attempts": {}, "observations": {}, "provenance": {}, "outbox": {}}
        self.fail_at = fail_at

    @contextmanager
    def transaction(self):
        working = deepcopy(self.state)
        yield MemoryTransaction(working, self.fail_at)
        self.state = deepcopy(working)


class MemoryTransaction:
    def __init__(self, state, fail_at):
        self.state, self.fail_at = state, fail_at

    def add_attempt(self, attempt):
        existing = self.state["attempts"].get(attempt.fingerprint)
        if existing:
            return existing.attempt_id, False
        self.state["attempts"][attempt.fingerprint] = attempt
        return attempt.attempt_id, True

    def add_observation(self, observation, attempt_id, fingerprint, created_at):
        existing = self.state["observations"].get(fingerprint)
        if existing:
            return existing["observation_id"], False
        if any(row["observation_id"] == observation["observation_id"]
               for row in self.state["observations"].values()):
            raise ValueError("Observation ID conflict")
        assert any(a.attempt_id == attempt_id for a in self.state["attempts"].values())
        self.state["observations"][fingerprint] = deepcopy(observation)
        return observation["observation_id"], True

    def add_provenance(self, observation_id, capture):
        if self.fail_at == "provenance":
            raise OSError("Injected provenance storage failure")
        self.state["provenance"][observation_id] = deepcopy(capture)

    def enqueue(self, event, created_at):
        if self.fail_at == "outbox":
            raise OSError("Injected outbox storage failure")
        observation_id = event["payload"]["observation_id"]
        assert any(row["observation_id"] == observation_id for row in self.state["observations"].values())
        self.state["outbox"][event["event_id"]] = deepcopy(event)
