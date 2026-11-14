"""Apply services/goal-intelligence/migrations/*.sql to PostgreSQL, in order, once each.

    GI_DATABASE_URL=postgresql://... python -m goal_intelligence.persistence.migrate

Applied versions are recorded in ``gi_schema_migrations``. A migration file runs
in autocommit mode so its own BEGIN/COMMIT controls the transaction.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def pending(applied: set[str]) -> list[Path]:
    return [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.stem not in applied]


def migrate(dsn: str) -> list[str]:
    import psycopg

    done: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gi_schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # One migrator at a time (e.g. several replicas starting together).
        conn.execute("SELECT pg_advisory_lock(hashtext('goal_intelligence_migrations'))")
        try:
            applied = {row[0] for row in conn.execute("SELECT version FROM gi_schema_migrations")}
            for path in pending(applied):
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO gi_schema_migrations (version) VALUES (%s)", (path.stem,))
                done.append(path.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('goal_intelligence_migrations'))")
    return done


def main() -> int:
    dsn = os.environ.get("GI_DATABASE_URL", "")
    if not dsn.startswith(("postgresql://", "postgres://")):
        print("GI_DATABASE_URL must be a postgresql:// URL", file=sys.stderr)
        return 2
    applied = migrate(dsn)
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
