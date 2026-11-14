"""Durable, DM-authenticated seller representations for the internal checker.

The representation is transported separately from the normalized assertion.  It
is immutable, keyed by the extraction attempt, and can therefore be replayed
without allowing a caller to label its own Observation as checked.
"""

from collections.abc import Callable
from datetime import datetime
import json
from uuid import UUID

from .checking import SellerRepresentation
from .ledger import AttemptConflict, digest, timestamp
from .postgres import Connection


class PostgresProofStore:
    def __init__(self, connect: Callable[[], Connection]) -> None:
        self.connect = connect

    @staticmethod
    def _payload(proof: SellerRepresentation) -> dict:
        return {
            "source_url": proof.source_url,
            "fetched_at": timestamp(proof.fetched_at),
            "cache_age_ms": proof.cache_age_ms,
            "provider_request_id": proof.provider_request_id,
            "json_ld": proof.json_ld,
            "resolved_addresses": list(proof.resolved_addresses),
            "access_policy_ids": list(proof.access_policy_ids),
        }

    def put(self, attempt_id: UUID, source_id: UUID, proof: SellerRepresentation) -> None:
        if not isinstance(attempt_id, UUID) or not isinstance(source_id, UUID):
            raise ValueError("Proof identities must be UUIDs")
        payload = self._payload(proof)
        fingerprint = digest(payload)
        connection = self.connect()
        try:
            if connection.autocommit:
                raise ValueError("Proof writes require autocommit=False")
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT source_id,source_url,fetched_at,cache_age_ms,provider_request_id
                       FROM extraction_attempts WHERE attempt_id=%s FOR SHARE""",
                    (str(attempt_id),),
                )
                attempt = cursor.fetchone()
                expected = (source_id, proof.source_url, proof.fetched_at,
                            proof.cache_age_ms, proof.provider_request_id)
                if attempt is None or tuple(attempt) != expected:
                    raise AttemptConflict("seller proof does not match extraction attempt")
                cursor.execute(
                    """INSERT INTO evidence_capture_proofs
                       (attempt_id,source_url,fetched_at,cache_age_ms,
                        provider_request_id,json_ld,resolved_addresses,access_policy_ids,
                        proof_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                       ON CONFLICT DO NOTHING RETURNING attempt_id""",
                    (str(attempt_id), proof.source_url, proof.fetched_at,
                     proof.cache_age_ms, proof.provider_request_id, proof.json_ld,
                     json.dumps(list(proof.resolved_addresses)),
                     json.dumps(list(proof.access_policy_ids)), fingerprint),
                )
                created = cursor.fetchone()
                if created is None:
                    cursor.execute(
                        "SELECT proof_hash FROM evidence_capture_proofs WHERE attempt_id=%s",
                        (str(attempt_id),),
                    )
                    row = cursor.fetchone()
                    if row is None or row[0] != fingerprint:
                        raise AttemptConflict("attempt_id reused for different seller proof")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load(self, observation_id: UUID) -> SellerRepresentation | None:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT p.source_url,p.fetched_at,p.cache_age_ms,
                              p.provider_request_id,p.json_ld,p.resolved_addresses,
                              p.access_policy_ids
                       FROM observations o
                       JOIN evidence_capture_proofs p
                         ON p.attempt_id=o.extraction_attempt_id
                       WHERE o.observation_id=%s""",
                    (str(observation_id),),
                )
                row = cursor.fetchone()
            connection.rollback()
            if row is None:
                return None
            return SellerRepresentation(
                source_url=row[0], fetched_at=row[1], cache_age_ms=row[2],
                provider_request_id=row[3], json_ld=row[4],
                resolved_addresses=tuple(row[5]), access_policy_ids=tuple(row[6]),
            )
        finally:
            connection.close()


class EvidenceCheckReads:
    """Expose only bounded checker facts needed by the future D4 consumer."""

    def __init__(self, connect: Callable[[], Connection]) -> None:
        self.connect = connect

    def by_observation(self, observation_id: UUID) -> dict | None:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT check_id,decision,reasons,policy_version,
                              normalizer_version,checked_at,proof->'claims'
                       FROM evidence_checks
                       WHERE result_observation_id=%s
                       ORDER BY checked_at DESC,check_id DESC LIMIT 1""",
                    (str(observation_id),),
                )
                row = cursor.fetchone()
            connection.rollback()
            if row is None:
                return None
            claims = row[6] if isinstance(row[6], dict) else {}
            return {
                "check_id": str(row[0]), "observation_id": str(observation_id),
                "decision": row[1], "reasons": row[2], "policy_version": row[3],
                "normalizer_version": row[4], "checked_at": timestamp(row[5]),
                "condition": claims.get("condition"),
            }
        finally:
            connection.close()
