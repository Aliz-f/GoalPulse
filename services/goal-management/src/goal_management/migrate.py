"""Apply Goal Management Alembic migrations without creating or dropping DBs."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def run() -> None:
    database_url = os.environ.get("GOAL_MANAGEMENT_DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("GOAL_MANAGEMENT_DATABASE_URL must be a PostgreSQL URL")
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
    command.upgrade(config, "head")


if __name__ == "__main__":
    run()
