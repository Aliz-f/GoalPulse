"""PostgreSQL integration tests (D2-GI-01 on the real database).

Skipped unless GI_TEST_POSTGRES_URL points at a server where the user may create
databases, e.g. the local compose stack:

    GI_TEST_POSTGRES_URL=postgresql://goalpulse_admin:<pw>@127.0.0.1:5432/postgres

Each run creates a throwaway database, applies migrations/*.sql, and drops it.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import USER_A, USER_B, converse

ADMIN_URL = os.environ.get("GI_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="GI_TEST_POSTGRES_URL not set; Postgres integration NOT RUN")

if ADMIN_URL:
    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

from goal_intelligence.llm.base import ResilientExtractor  # noqa: E402
from goal_intelligence.llm.fixture import FixtureExtractor  # noqa: E402
from goal_intelligence.persistence.migrate import migrate  # noqa: E402
from goal_intelligence.persistence.repository import (  # noqa: E402
    ConfirmConflict,
    Message,
    RevisionData,
    SessionClosed,
    SessionNotFound,
    StaleRevision,
    postgres_repository,
)
from goal_intelligence.service import GoalIntelligenceService  # noqa: E402
from test_gi_compiler import PRODUCT_FLOW  # noqa: E402


@pytest.fixture(scope="module")
def pg_url():
    name = f"gi_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    params = conninfo_to_dict(ADMIN_URL)
    params["dbname"] = name
    url = make_conninfo(**params)
    try:
        assert migrate(url) == ["0001_init"]
        assert migrate(url) == []  # idempotent
        yield url
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def pg_repo(pg_url):
    return postgres_repository(pg_url)


def rev(state="collecting", ready=False):
    return RevisionData(
        state=state, candidate_goal={"goal_type": "product_purchase", "criteria": {"max_total_eur_cents": 60000}},
        missing_fields=[] if ready else ["product_query"], next_question=None if ready else "Which product?",
        ready=ready, working_state={"goal_type": "product_purchase"},
    )


def test_roundtrip_types_and_ownership(pg_repo):
    rec = pg_repo.create_session(USER_A, rev(), [Message("user", "hi"), Message("assistant", "Which product?")])
    got = pg_repo.get_current(rec.session_id, USER_A)
    assert got.revision == 1 and got.owner_user_id == USER_A
    assert got.candidate_goal == {"goal_type": "product_purchase", "criteria": {"max_total_eur_cents": 60000}}
    assert isinstance(got.candidate_goal["criteria"]["max_total_eur_cents"], int)
    assert got.expires_at.tzinfo is not None
    assert [m.role for m in pg_repo.transcript(rec.session_id)] == ["user", "assistant"]
    with pytest.raises(SessionNotFound):
        pg_repo.get_current(rec.session_id, USER_B)


def test_parallel_writes_one_wins_one_stale(pg_repo):
    rec = pg_repo.create_session(USER_A, rev(), [])
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def writer(tag: str) -> None:
        barrier.wait()
        try:
            pg_repo.append_revision(rec.session_id, USER_A, 1, rev(), [Message("user", tag)])
            outcomes.append("ok")
        except StaleRevision as exc:
            assert exc.current_revision == 2
            outcomes.append("stale")

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ok", "stale"]
    assert pg_repo.get_current(rec.session_id, USER_A).revision == 2
    assert len(pg_repo.transcript(rec.session_id)) == 1


def test_revisions_immutable_in_postgres(pg_repo, pg_url):
    rec = pg_repo.create_session(USER_A, rev(), [])
    with psycopg.connect(pg_url) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            conn.execute("UPDATE draft_revisions SET next_question = 'x' WHERE session_id = %s", (rec.session_id,))
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            conn.execute("DELETE FROM draft_revisions WHERE session_id = %s", (rec.session_id,))


def test_mark_confirmed_idempotent_and_check_constraints(pg_repo):
    rec = pg_repo.create_session(USER_A, rev("ready_for_confirmation", ready=True), [])
    goal = str(uuid.uuid4())
    pg_repo.mark_confirmed(rec.session_id, 1, goal)
    pg_repo.mark_confirmed(rec.session_id, 1, goal)
    with pytest.raises(ConfirmConflict):
        pg_repo.mark_confirmed(rec.session_id, 1, str(uuid.uuid4()))
    with pytest.raises(SessionClosed):
        pg_repo.append_revision(rec.session_id, USER_A, 1, rev(), [])


def test_expiry_uses_timestamptz(pg_url):
    now = [datetime(2026, 11, 14, 10, tzinfo=UTC)]
    repo = postgres_repository(pg_url, clock=lambda: now[0], session_ttl=timedelta(hours=1))
    rec = repo.create_session(USER_A, rev(), [])
    now[0] += timedelta(hours=2)
    with pytest.raises(SessionClosed):
        repo.append_revision(rec.session_id, USER_A, 1, rev(), [])


def test_full_product_flow_on_postgres(pg_repo, goal_spec_validator):
    svc = GoalIntelligenceService(pg_repo, ResilientExtractor(FixtureExtractor()))
    draft = converse(svc, USER_A, PRODUCT_FLOW)
    assert draft.ready_for_confirmation and draft.revision == len(PRODUCT_FLOW)
    candidate = svc.get_candidate(draft.session_id, draft.revision)
    spec = dict(candidate.candidate_goal)
    spec.update(goal_id=str(uuid.uuid4()), owner_user_id=USER_A, version=1, approved_at="2026-11-14T12:00:00Z")
    goal_spec_validator.validate(spec)
    svc.mark_confirmed(draft.session_id, spec["goal_id"], draft.revision)
    assert svc.get_session(USER_A, draft.session_id).state == "confirmed"
