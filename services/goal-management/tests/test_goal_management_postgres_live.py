"""Opt-in acceptance test against an explicitly disposable GM database."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

import psycopg
import pytest

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "services/goal-management/src"))

from goal_management.repository import DatabaseRepository, GoalVersionRecord


DATABASE_URL = os.environ.get("GOAL_MANAGEMENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="GOAL_MANAGEMENT_TEST_DATABASE_URL is not set to a disposable migrated database",
)


def test_restart_replay_projection_ownership_and_immutability():
    owner = uuid.uuid4()
    other_owner = uuid.uuid4()
    goal_id = uuid.uuid4()
    session_id = uuid.uuid4()
    approved_at = datetime.now(timezone.utc)
    spec = json.loads((ROOT / "contracts/examples/goal-spec.product.json").read_text())
    spec.update(
        goal_id=str(goal_id),
        owner_user_id=str(owner),
        version=1,
        approved_at=approved_at.isoformat().replace("+00:00", "Z"),
    )
    record = GoalVersionRecord(
        goal_id=goal_id,
        owner_user_id=owner,
        confirmed_session_id=session_id,
        version=1,
        spec=spec,
        approved_at=approved_at,
        approved_draft_revision=2,
        correlation_id=uuid.uuid4(),
    )

    first_process = DatabaseRepository(DATABASE_URL)
    assert first_process.create_version_one(record) == spec

    # A new repository represents an API restart. Exact session/revision replay
    # returns the immutable goal and cannot create another activation intent.
    restarted = DatabaseRepository(DATABASE_URL)
    replay_goal_id = uuid.uuid4()
    replay = GoalVersionRecord(
        **{
            **record.__dict__,
            "goal_id": replay_goal_id,
            "spec": {**spec, "goal_id": str(replay_goal_id)},
        }
    )
    assert restarted.create_version_one(replay) == spec
    assert restarted.get_confirmed_for_session(session_id) == (spec, 2, owner)
    with psycopg.connect(DATABASE_URL) as connection:
        count = connection.execute(
            "SELECT count(*) FROM outbox WHERE payload_jsonb->'payload'->>'goal_id'=%s",
            (str(goal_id),),
        ).fetchone()[0]
    assert count == 1

    occurred_at = datetime.now(timezone.utc)
    assert restarted.process_evaluated(
        event_id=str(uuid.uuid4()),
        event_type="goal.evaluated.v1",
        goal_id=str(goal_id),
        goal_version=1,
        evaluation_id=str(uuid.uuid4()),
        status="satisfied",
        occurred_at=occurred_at,
    ) == "projected"
    assert restarted.process_evaluated(
        event_id=str(uuid.uuid4()),
        event_type="goal.evaluated.v1",
        goal_id=str(goal_id),
        goal_version=2,
        evaluation_id=str(uuid.uuid4()),
        status="unknown",
        occurred_at=occurred_at,
    ) == "stale"
    summary = restarted.list_for_owner(owner, None)[0]
    assert summary["opportunity_status"] == "verified"
    assert summary["version"] == 1
    assert restarted.get_for_owner(other_owner, goal_id) is None

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            connection.execute(
                "UPDATE goal_versions SET approved_draft_revision=3 WHERE goal_id=%s AND version=1",
                (goal_id,),
            )
