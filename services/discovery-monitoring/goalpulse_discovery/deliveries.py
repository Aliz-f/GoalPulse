"""Owned durable source plans and exact DE HTTP publication intents.

The DE idempotency key and submitted JSON survive a DM worker crash. Receipt
updates never rewrite the capture, so an ambiguous HTTP timeout is safely replayed
without another provider scrape or a different assertion under the same key.
"""

from copy import deepcopy
from datetime import timezone
from hashlib import sha256
import json
from uuid import UUID

from .adapters import extraction_submission
from .context_provider import public_addresses


def _encoded(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class SubmissionStore:
    def __init__(self, database) -> None:
        self.database = database

    def load_plan(self, goal_id: UUID, version: int) -> list[str] | None:
        with self.database.transaction() as cursor:
            cursor.execute("SELECT urls FROM discovery_plans WHERE goal_id=%s AND goal_version=%s",
                           (str(goal_id), version))
            row = cursor.fetchone()
            return deepcopy(row[0]) if row else None

    def save_plan(self, goal_id: UUID, version: int, urls: list[str]) -> list[str]:
        if type(version) is not int or version < 1 or len(urls) > 5 or any(not isinstance(u, str) for u in urls):
            raise ValueError("Invalid initial source plan")
        with self.database.transaction() as cursor:
            cursor.execute("""INSERT INTO discovery_plans (goal_id,goal_version,urls)
                              VALUES (%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                           (str(goal_id), version, _encoded(urls)))
            cursor.execute("SELECT urls FROM discovery_plans WHERE goal_id=%s AND goal_version=%s",
                           (str(goal_id), version))
            return deepcopy(cursor.fetchone()[0])

    def get(self, attempt_id: UUID) -> dict | None:
        with self.database.transaction() as cursor:
            cursor.execute("SELECT submission,receipt,capture_metadata FROM extraction_deliveries WHERE attempt_id=%s",
                           (str(attempt_id),))
            row = cursor.fetchone()
            return {"submission": deepcopy(row[0]), "receipt": deepcopy(row[1]),
                    "capture_metadata": deepcopy(row[2])} if row else None

    def put(self, submission: dict, *, capture_metadata: dict | None = None) -> dict:
        body = _encoded(submission)
        fingerprint = sha256(body.encode()).hexdigest()
        with self.database.transaction() as cursor:
            cursor.execute("""INSERT INTO extraction_deliveries
                              (attempt_id,source_id,submission,submission_hash,capture_metadata)
                              VALUES (%s,%s,%s::jsonb,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                           (submission["attempt_id"], submission["source_id"], body, fingerprint,
                            _encoded(capture_metadata or {})))
            cursor.execute("SELECT submission,receipt,submission_hash,capture_metadata FROM extraction_deliveries WHERE attempt_id=%s",
                           (submission["attempt_id"],))
            row = cursor.fetchone()
            if row[2] != fingerprint:
                raise ValueError("Attempt identity reused for different extraction")
            if row[3] != (capture_metadata or {}):
                raise ValueError("Attempt identity reused for different capture proof")
            return {"submission": deepcopy(row[0]), "receipt": deepcopy(row[1]),
                    "capture_metadata": deepcopy(row[3])}

    def complete(self, attempt_id: UUID, receipt: dict) -> None:
        with self.database.transaction() as cursor:
            cursor.execute("""UPDATE extraction_deliveries SET receipt=%s::jsonb,delivered_at=now()
                              WHERE attempt_id=%s AND receipt IS NULL""", (_encoded(receipt), str(attempt_id)))


class JournaledEvidenceSink:
    def __init__(self, client, store, *, resolver=public_addresses) -> None:
        self.client, self.store, self.resolver = client, store, resolver

    def _deliver(self, record: dict) -> dict:
        if record["receipt"] is not None:
            return record["receipt"]
        body = record["submission"]
        proof = record.get("capture_metadata", {}).get("seller_proof")
        receipt = (self.client.send_checked_submission(body, proof)
                   if isinstance(proof, dict) else self.client.send_submission(body))
        self.store.complete(UUID(body["attempt_id"]), receipt)
        return receipt

    def resume(self, attempt_id: UUID) -> dict | None:
        record = self.store.get(attempt_id)
        return self._deliver(record) if record is not None else None

    def submit(self, source, result, *, attempt_id: UUID, correlation_id: UUID) -> dict:
        body = extraction_submission(source, result, attempt_id=attempt_id,
                                     correlation_id=correlation_id, resolver=self.resolver)
        def time_value(value):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None
        metadata = {"requested_at": time_value(getattr(result, "requested_at", None)),
                    "fetched_at": time_value(result.fetched_at),
                    "provider_observed_at": time_value(getattr(result, "provider_observed_at", None)),
                    "observed_at": time_value(result.observed_at), "cache_age_ms": result.cache_age_ms,
                    "provider_request_id": result.request_id, "source_url": result.source_url}
        if (getattr(result, "seller_representation", None) is not None
                and getattr(result, "resolved_addresses", ())
                and getattr(result, "access_policy_ids", ())):
            metadata["seller_proof"] = {
                "seller_representation": result.seller_representation,
                "resolved_addresses": list(result.resolved_addresses),
                "access_policy_ids": list(result.access_policy_ids),
            }
        return self._deliver(self.store.put(body, capture_metadata=metadata))
