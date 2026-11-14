"""Local operator authorization and owned SQL boundary checks (not DB proof)."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from goalpulse_discovery import __main__ as cli
from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.limits import UsageStore
from goalpulse_discovery.operator import current_local_principal, require_local_operator
from goalpulse_discovery.registry import SourceRegistry
from goalpulse_discovery.scheduling import ScanStore
from test_runtime_pipeline import GoalReads

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("allowed,actual", [("", "sid:admin"), ("sid:admin", "sid:guest"), ("uid:0", "uid:1000")])
def test_only_os_verified_allowlisted_local_operator_is_admitted(allowed, actual):
    with pytest.raises(PermissionError):
        require_local_operator(allowed, identity_reader=lambda: actual)


def test_operator_identity_is_derived_from_os_and_never_from_username_environment(monkeypatch):
    monkeypatch.setenv("USERNAME", "Administrator")
    monkeypatch.setenv("USER", "root")
    assert require_local_operator("sid:operator", identity_reader=lambda: "sid:operator") == "sid:operator"
    # Exercise real platform token acquisition, without logging the user's SID.
    actual = current_local_principal()
    assert actual.startswith(("sid:S-", "uid:"))


@pytest.mark.parametrize("command", ["check-now", "schedule-status"])
def test_unprivileged_cli_denied_before_runtime_or_credentials_load(monkeypatch, capsys, command):
    monkeypatch.delenv("DM_OPERATOR_IDS", raising=False)
    load = Mock(side_effect=AssertionError("must not load runtime"))
    monkeypatch.setattr(cli, "from_env", load)
    assert cli.main([command, "--goal-id", str(uuid4()), "--request-id", str(uuid4())]) == 2
    load.assert_not_called()
    assert capsys.readouterr().out.strip() == "operator=forbidden"


def test_privileged_check_now_queues_common_job_with_stable_request_identity(monkeypatch, capsys):
    goal, request = uuid4(), uuid4()
    runtime = SimpleNamespace(check_now=Mock(return_value={"status": "queued", "scan_id": str(uuid4())}), close=Mock())
    monkeypatch.setenv("DM_OPERATOR_IDS", "sid:operator")
    monkeypatch.setattr(cli, "require_local_operator", lambda allowed: require_local_operator(allowed, identity_reader=lambda: "sid:operator"))
    monkeypatch.setattr(cli, "from_env", lambda: runtime)
    assert cli.main(["check-now", "--goal-id", str(goal), "--request-id", str(request)]) == 0
    runtime.check_now.assert_called_once_with(goal, request)
    assert '"status": "queued"' in capsys.readouterr().out
    runtime.close.assert_called_once()


class ScriptedDatabase:
    def __init__(self, rows=(), many=()):
        self.rows, self.many, self.calls = list(rows), many, []
        self.commits = self.rollbacks = 0
    @contextmanager
    def transaction(self):
        try:
            yield self
            self.commits += 1
        except Exception:
            self.rollbacks += 1
            raise
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
    def fetchone(self):
        return self.rows.pop(0)
    def fetchall(self):
        return self.many


def test_hundredth_user_reservation_guard_rolls_back_before_provider_intent():
    db = ScriptedDatabase([(0, 0, 0), (100,)])
    owner = uuid4()
    with pytest.raises(ProviderError, match="USER_DAILY_CALL_LIMIT"):
        UsageStore(db).reserve("a" * 64, NOW, ttl_seconds=1, owner_user_id=owner)
    assert db.rollbacks == 1
    assert not any("INSERT INTO provider_operations" in sql for sql, _ in db.calls)
    query, params = next((sql, params) for sql, params in db.calls if "SELECT operations FROM provider_user_day" in sql)
    assert "FOR UPDATE" in query and params == (str(owner), NOW.date())


def test_user_call_counter_and_ledger_are_committed_together_after_cooldown_check():
    owner = uuid4()
    db = ScriptedDatabase([(0, 0, 0), (99,), (NOW,)])
    UsageStore(db).reserve("a" * 64, NOW, ttl_seconds=1, owner_user_id=owner)
    assert db.commits == 1
    assert any("UPDATE provider_user_day SET operations=operations+1" in sql for sql, _ in db.calls)
    insert = next(params for sql, params in db.calls if "INSERT INTO provider_operations" in sql)
    assert insert[-1] == str(owner)


def test_active_slot_admission_is_locked_per_authenticated_owner():
    db = ScriptedDatabase([None, (2,)])
    goal = GoalReads().goal
    reason = ScanStore(db, clock=lambda: NOW).admit(goal, initial=True)
    assert reason == "ACTIVE_GOAL_LIMIT"
    assert db.calls[0][1] == ("admission:" + goal["owner_user_id"],)
    assert any("FOR UPDATE" in sql for sql, _ in db.calls)
    insert = next(params for sql, params in db.calls if "INSERT INTO scan_schedules" in sql)
    assert insert[4:7] == ("blocked", "ACTIVE_GOAL_LIMIT", None)


def test_source_subscription_replacement_cannot_exceed_three_or_cross_goal():
    active = [uuid4() for _ in range(3)]
    db = ScriptedDatabase(many=[(identifier,) for identifier in active])
    goal, source = uuid4(), uuid4()
    SourceRegistry(db).subscribe(goal, 7, source)
    removals = [(sql, params) for sql, params in db.calls if "SET state='inactive'" in sql]
    assert len(removals) == 1 and removals[0][1] == (str(goal), 7, str(active[0]))
    assert "goal_id=%s AND goal_version=%s AND source_id=%s" in removals[0][0]


def test_due_claim_lock_allows_nested_immutable_plan_foreign_key_write():
    # FOR NO KEY UPDATE is deliberate: plan FK KEY SHARE checks must not wait
    # on the same worker's outer claim transaction through another connection.
    db = ScriptedDatabase([None])
    assert ScanStore(db).run_once(Mock()) == "idle"
    assert "FOR NO KEY UPDATE SKIP LOCKED" in db.calls[0][0]


def test_reconciliation_cannot_restore_evicted_sources_above_limit():
    db = ScriptedDatabase()
    SourceRegistry(db).reconcile(uuid4(), current_version=2, lifecycle="active")
    sql, params = db.calls[-1]
    assert "LIMIT 3" in sql and "ORDER BY updated_at DESC,source_id" in sql
    assert "updated_at=now()" not in sql  # Preserve subscription recency on resume.
    assert params[0:2] == (2, "active")


def test_no_public_check_now_or_webhook_route_is_registered():
    from goalpulse_discovery.api import create_app
    paths = {route.path for route in create_app(object()).routes}
    assert not any("webhook" in path or "check-now" in path or path.startswith("/v1/") for path in paths)
