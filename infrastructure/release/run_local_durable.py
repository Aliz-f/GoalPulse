#!/usr/bin/env python3
"""Run the durable lane against the isolated Compose Postgres/Rabbit services."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _read_env(path: Path) -> dict[str, str]:
    target = path.resolve()
    if Path("/tmp") not in target.parents:
        raise SystemExit("local durable environment must be below /tmp")
    values: dict[str, str] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name:
            raise SystemExit("invalid environment file")
        values[name] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = _read_env(args.env_file)
    postgres = (
        f"postgresql://{values['POSTGRES_USER']}:{values['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:{values['POSTGRES_HOST_PORT']}/"
    )
    rabbit = (
        f"amqp://{values['RABBITMQ_DEFAULT_USER']}:{values['RABBITMQ_DEFAULT_PASS']}"
        f"@127.0.0.1:{values['RABBITMQ_HOST_PORT']}/%2F"
    )
    environment = os.environ | {
        "PHASE2_CI_POSTGRES_ADMIN_URL": postgres + "postgres",
        "PHASE2_CI_RABBITMQ_URL": rabbit,
        "GI_TEST_POSTGRES_URL": postgres + "postgres",
        "GOAL_MANAGEMENT_TEST_DATABASE_URL": postgres + "goal_management_test",
        "GOALPULSE_DM_TEST_DSN": postgres + "discovery_monitoring_test",
        "GOALPULSE_DE_TEST_DSN": postgres + "data_evidence_test",
        "GOALPULSE_GE_TEST_DSN": postgres + "postgres",
        "GOALPULSE_GE_TEST_AMQP_URL": rabbit,
        "GOALPULSE_NS_TEST_DSN": postgres + "postgres",
        "GOALPULSE_AI_TEST_DSN": postgres + "postgres",
        "GOALPULSE_AI_TEST_AMQP_URL": rabbit,
    }
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("bootstrap_ci_services.py"))],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    migration_environment = environment | {
        "GOAL_MANAGEMENT_DATABASE_URL": postgres + "goal_management_test",
        "PYTHONPATH": str(ROOT / "services/goal-management/src"),
    }
    subprocess.run(
        [sys.executable, "-m", "goal_management.migrate"],
        cwd=ROOT,
        env=migration_environment,
        check=True,
    )
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("run_phase2_gate.py")),
            "--lane",
            "durable",
            "--output",
            str(args.output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
