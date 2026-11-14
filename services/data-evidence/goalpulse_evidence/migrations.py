"""Forward-only, checksummed migrations; caller supplies an owned DB connection."""

from hashlib import sha256
from pathlib import Path

from .postgres import Connection


def apply_migrations(connection: Connection, directory: Path) -> list[str]:
    """Apply in one transaction under a service-specific PostgreSQL advisory lock.

    Requires autocommit=False. Checksums normalize newlines across platforms.
    Existing version checksums cannot be silently changed.
    The caller owns and closes the connection. No database is created or dropped.
    """
    applied = []
    try:
        if connection.autocommit:
            raise ValueError("Migrations require autocommit=False")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(734901601)")
            cursor.execute("""CREATE TABLE IF NOT EXISTS de_schema_migrations (
                version text PRIMARY KEY, checksum char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now())""")
            for path in sorted(directory.glob("[0-9]*.sql")):
                sql = path.read_text(encoding="utf-8")
                checksum = sha256(sql.encode()).hexdigest()
                cursor.execute("SELECT checksum FROM de_schema_migrations WHERE version = %s", (path.name,))
                row = cursor.fetchone()
                if row:
                    if row[0] != checksum:
                        raise ValueError(f"Applied migration checksum changed: {path.name}")
                    continue
                cursor.execute(sql)
                cursor.execute("INSERT INTO de_schema_migrations (version, checksum) VALUES (%s,%s)",
                               (path.name, checksum))
                applied.append(path.name)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return applied
