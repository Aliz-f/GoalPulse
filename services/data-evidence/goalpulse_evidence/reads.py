"""Strict service-private evidence queries. M2M HTTP/auth awaits ICP-01/08.

There is deliberately no caller-supplied identity or homemade bearer verifier.
Only future authenticated handlers may call this storage component.
"""

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from .ledger import EvidenceLedger
from .postgres import Connection


class EvidenceReads:
    def __init__(self, connect: Callable[[], Connection], *, schema_directory: Path) -> None:
        self.connect = connect
        self.validator = EvidenceLedger._validator(schema_directory / "observation.v1.schema.json")

    def _query(self, sql: str, params: tuple, *, many: bool) -> list[dict]:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall() if many else [cursor.fetchone()]
            result = []
            for row in rows:
                if row is not None:
                    self.validator.validate(row[0])
                    result.append(deepcopy(row[0]))
            connection.rollback()  # Close the read transaction without mutating anything.
            return result
        finally:
            connection.close()

    def by_id(self, observation_id: UUID) -> dict | None:
        if not isinstance(observation_id, UUID):
            raise ValueError("observation_id must be UUID")
        results = self._query("SELECT payload FROM observations WHERE observation_id = %s",
                              (str(observation_id),), many=False)
        return results[0] if results else None

    def by_source(self, source_id: UUID, *, limit: int = 20) -> list[dict]:
        if not isinstance(source_id, UUID) or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("source_id must be UUID and limit between 1 and 100")
        return self._query("""SELECT payload FROM observations o WHERE source_id = %s
                              AND NOT EXISTS (SELECT 1 FROM evidence_checks c
                                              WHERE c.original_observation_id = o.observation_id
                                                AND c.result_observation_id IS NOT NULL)
                              AND (o.reliability <> 'checked' OR NOT EXISTS (
                                  SELECT 1 FROM observations x
                                  WHERE x.source_id = o.source_id
                                    AND x.observation_id <> o.observation_id
                                    AND x.observed_at >= o.observed_at
                                    AND NOT EXISTS (SELECT 1 FROM evidence_checks xc
                                                    WHERE xc.original_observation_id = x.observation_id
                                                      AND xc.result_observation_id IS NOT NULL)
                                    AND (
                                        x.payload->'product_name' IS DISTINCT FROM o.payload->'product_name'
                                        OR x.payload->'unit_price_eur_cents' IS DISTINCT FROM o.payload->'unit_price_eur_cents'
                                        OR (NULLIF(x.payload->>'model', '') IS NOT NULL
                                            AND NULLIF(o.payload->>'model', '') IS NOT NULL
                                            AND x.payload->'model' IS DISTINCT FROM o.payload->'model')
                                        OR (x.payload->>'availability' <> 'unknown'
                                            AND o.payload->>'availability' <> 'unknown'
                                            AND x.payload->'availability' IS DISTINCT FROM o.payload->'availability')
                                        OR (x.payload->>'brand' IS NOT NULL AND o.payload->>'brand' IS NOT NULL
                                            AND x.payload->'brand' IS DISTINCT FROM o.payload->'brand')
                                        OR (x.payload->>'delivery_to_fi' <> 'unknown'
                                            AND o.payload->>'delivery_to_fi' <> 'unknown'
                                            AND x.payload->'delivery_to_fi' IS DISTINCT FROM o.payload->'delivery_to_fi')
                                        OR (x.payload->>'shipping_eur_cents' IS NOT NULL
                                            AND o.payload->>'shipping_eur_cents' IS NOT NULL
                                            AND x.payload->'shipping_eur_cents' IS DISTINCT FROM o.payload->'shipping_eur_cents')
                                        OR (x.payload->>'tax_eur_cents' IS NOT NULL
                                            AND o.payload->>'tax_eur_cents' IS NOT NULL
                                            AND x.payload->'tax_eur_cents' IS DISTINCT FROM o.payload->'tax_eur_cents')
                                    )
                              ))
                              ORDER BY observed_at DESC, created_at DESC, observation_id DESC LIMIT %s""",
                           (str(source_id), limit), many=True)
