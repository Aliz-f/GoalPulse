"""D2-GI-01: per-user ownership, optimistic concurrency and immutable revisions."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest
from conftest import USER_A, USER_B

from goal_intelligence.persistence.repository import (
    ConfirmConflict,
    Message,
    RevisionData,
    SessionClosed,
    SessionNotFound,
    StaleRevision,
    sqlite_repository,
)


def rev(state="collecting", q="Which product?", ready=False):
    return RevisionData(
        state=state, candidate_goal={"goal_type": "product_purchase"},
        missing_fields=[] if ready else ["product_query"], next_question=None if ready else q,
        ready=ready, working_state={},
    )


def test_create_and_read_own_session(repo):
    rec = repo.create_session(USER_A, rev(), [Message("user", "hello"), Message("assistant", "Which product?")])
    assert rec.revision == 1 and rec.owner_user_id == USER_A
    assert repo.get_current(rec.session_id, USER_A).next_question == "Which product?"
    assert [m.role for m in repo.transcript(rec.session_id)] == ["user", "assistant"]


def test_foreign_session_is_not_readable(repo):
    rec = repo.create_session(USER_A, rev(), [])
    with pytest.raises(SessionNotFound):
        repo.get_current(rec.session_id, USER_B)
    with pytest.raises(SessionNotFound):
        repo.append_revision(rec.session_id, USER_B, 1, rev(), [])


def test_parallel_writes_same_expected_revision_one_wins(tmp_path):
    repo = sqlite_repository(tmp_path / "race.sqlite3")
    rec = repo.create_session(USER_A, rev(), [])
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def writer(tag: str) -> None:
        barrier.wait()
        try:
            repo.append_revision(rec.session_id, USER_A, 1, rev(q=f"from {tag}"), [Message("user", tag)])
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
    current = repo.get_current(rec.session_id, USER_A)
    assert current.revision == 2
    # the loser's message was rolled back with its revision: no lost or orphaned update
    assert len(repo.transcript(rec.session_id)) == 1


def test_stale_write_after_sequential_update(repo):
    rec = repo.create_session(USER_A, rev(), [])
    repo.append_revision(rec.session_id, USER_A, 1, rev(), [])
    with pytest.raises(StaleRevision):
        repo.append_revision(rec.session_id, USER_A, 1, rev(), [])


def test_revisions_are_immutable(repo, tmp_path):
    rec = repo.create_session(USER_A, rev(), [])
    conn = sqlite3.connect(tmp_path / "gi.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE draft_revisions SET next_question = 'x' WHERE session_id = ?", (rec.session_id,))
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("DELETE FROM draft_revisions WHERE session_id = ?", (rec.session_id,))
    conn.close()


def test_mark_confirmed_is_idempotent_and_closes_session(repo):
    rec = repo.create_session(USER_A, rev(state="ready_for_confirmation", ready=True), [])
    goal = "ae000000-0000-4000-8000-0000000000f1"
    repo.mark_confirmed(rec.session_id, 1, goal)
    repo.mark_confirmed(rec.session_id, 1, goal)  # replay is a no-op
    with pytest.raises(ConfirmConflict):
        repo.mark_confirmed(rec.session_id, 1, "ae000000-0000-4000-8000-0000000000f2")
    with pytest.raises(SessionClosed):
        repo.append_revision(rec.session_id, USER_A, 1, rev(), [])
    assert repo.get_current(rec.session_id, USER_A).state == "confirmed"


def test_mark_confirmed_rejects_unready_or_stale(repo):
    rec = repo.create_session(USER_A, rev(), [])
    with pytest.raises(ConfirmConflict):
        repo.mark_confirmed(rec.session_id, 1, "ae000000-0000-4000-8000-0000000000f1")
    with pytest.raises(SessionNotFound):
        repo.mark_confirmed("ae000000-0000-4000-8000-0000000000ff", 1, "ae000000-0000-4000-8000-0000000000f1")


def test_expired_session_rejects_writes(tmp_path):
    now = [datetime(2026, 11, 14, 10, tzinfo=UTC)]
    repo = sqlite_repository(tmp_path / "exp.sqlite3", clock=lambda: now[0], session_ttl=timedelta(hours=1))
    rec = repo.create_session(USER_A, rev(), [])
    now[0] += timedelta(hours=2)
    with pytest.raises(SessionClosed):
        repo.append_revision(rec.session_id, USER_A, 1, rev(), [])
