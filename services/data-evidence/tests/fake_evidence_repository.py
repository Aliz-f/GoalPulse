"""Transactional test double only; PostgreSQL is the sole production backend."""

from contextlib import contextmanager
from copy import deepcopy

from goalpulse_evidence.ledger import AttemptConflict, parse_timestamp


class MemoryState(dict):
    """Row metadata is separate from the public snapshot dictionaries."""

    def __init__(self):
        super().__init__(attempts={}, observations={}, provenance={}, outbox={})
        self.observation_metadata = {}


class MemoryRepository:
    def __init__(self, fail_at=None):
        self.state = MemoryState()
        self.fail_at = fail_at

    @contextmanager
    def transaction(self):
        working = deepcopy(self.state)
        yield MemoryTransaction(working, self.fail_at)
        self.state = deepcopy(working)


class MemoryTransaction:
    def __init__(self, state, fail_at):
        self.state, self.fail_at = state, fail_at

    def lock_source(self, source_id):
        # Offline transactions are sequential. Production uses advisory DB locks.
        pass

    def get_observation(self, observation_id):
        return deepcopy(next((o for o in self.state["observations"].values()
                              if o["observation_id"] == observation_id), None))

    def get_capture(self, observation_id):
        return deepcopy(self.state["provenance"].get(observation_id))

    def get_attempt_id(self, observation_id):
        return self.state.observation_metadata[observation_id]["attempt_id"]

    def get_check(self, check_id):
        return deepcopy(self.state.get("checks", {}).get(check_id))

    def proof_used(self, proof_digest):
        return any(c["decision"] == "checked" and c["proof_digest"] == proof_digest
                   for c in self.state.get("checks", {}).values())

    def source_observations(self, source_id, since):
        superseded = {c["original_observation_id"] for c in self.state.get("checks", {}).values()
                      if c["result_observation_id"] is not None}
        observations = [o for o in self.state["observations"].values()
                        if o["source_id"] == source_id and parse_timestamp(o["observed_at"]) >= since
                        and o["observation_id"] not in superseded]
        return deepcopy(sorted(observations, reverse=True, key=lambda o: (
            parse_timestamp(o["observed_at"]),
            self.state.observation_metadata[o["observation_id"]]["created_at"], o["observation_id"])))

    def current_claims(self, source_id, observed_at):
        checks = self.state.get("checks", {})
        superseded = {c["original_observation_id"] for c in checks.values()
                      if c["result_observation_id"] is not None}
        result = []
        for check in sorted(checks.values(), reverse=True, key=lambda c: (c["checked_at"], c["check_id"])):
            claims = check["proof"].get("claims")
            observation = self.get_observation(check["result_observation_id"])
            if (check["decision"] == "checked" and isinstance(claims, dict)
                    and observation is not None and observation["source_id"] == source_id
                    and parse_timestamp(observation["observed_at"]) == observed_at
                    and observation["observation_id"] not in superseded):
                result.append(claims)
        return deepcopy(result)

    def add_check(self, audit):
        if self.fail_at == "check":
            raise OSError("Injected checker audit storage failure")
        checks = self.state.setdefault("checks", {})
        if audit["check_id"] in checks:
            raise ValueError("Check ID conflict")
        if audit["decision"] == "checked" and self.proof_used(audit["proof_digest"]):
            raise ValueError("Checked proof replay")
        checks[audit["check_id"]] = deepcopy(audit)

    def add_attempt(self, attempt):
        for existing in self.state["attempts"].values():
            if existing.attempt_id == attempt.attempt_id:
                if existing.fingerprint != attempt.fingerprint:
                    raise AttemptConflict("attempt_id was reused for different extraction data")
                return existing.attempt_id, False
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
        self.state.observation_metadata[observation["observation_id"]] = {
            "attempt_id": attempt_id, "created_at": created_at}
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
