"""D2-GI-04: public session semantics, GM-only candidate/mark-confirmed, and auth (service layer)."""

from __future__ import annotations

import uuid

import pytest
from conftest import USER_A, USER_B, converse

from goal_intelligence.auth import Authenticator, AuthError, Forbidden, parse_service_tokens
from goal_intelligence.service import Conflict, NotFound
from test_gi_compiler import PRODUCT_FLOW

GW = "gateway-secret-0123456789"
GM = "gm-secret-0123456789abcd"
DM = "dm-secret-0123456789abcd"
TOKENS = {"gateway": GW, "goal-management": GM, "discovery-monitoring": DM}


# ---------------------------------------------------------------- service layer


def test_create_returns_revision_1_owned_by_caller(service, goal_draft_validator):
    draft = service.create_session(USER_A, "I want to buy an RTX 5070")
    assert draft.revision == 1 and draft.owner_user_id == USER_A
    goal_draft_validator.validate(draft.model_dump())


def test_stale_expected_revision_is_409_with_current(service):
    draft = service.create_session(USER_A, "I want to buy an RTX 5070")
    service.post_message(USER_A, draft.session_id, "under 600 euros", 1)
    with pytest.raises(Conflict) as info:
        service.post_message(USER_A, draft.session_id, "yes", 1)
    assert info.value.current_revision == 2


def test_other_users_session_is_404(service):
    draft = service.create_session(USER_A, "I want to buy an RTX 5070")
    with pytest.raises(NotFound):
        service.get_session(USER_B, draft.session_id)
    with pytest.raises(NotFound):
        service.post_message(USER_B, draft.session_id, "yes", 1)


def test_candidate_requires_exact_ready_revision(service):
    draft = converse(service, USER_A, PRODUCT_FLOW[:-1])
    with pytest.raises(Conflict):  # not ready yet
        service.get_candidate(draft.session_id, draft.revision)
    draft = service.post_message(USER_A, draft.session_id, "yes", draft.revision)
    with pytest.raises(Conflict):  # stale
        service.get_candidate(draft.session_id, draft.revision - 1)
    got = service.get_candidate(draft.session_id, draft.revision)
    assert got == draft and got.owner_user_id == USER_A
    with pytest.raises(NotFound):
        service.get_candidate(str(uuid.uuid4()), 1)


def test_mark_confirmed_idempotent_then_session_closed(service):
    draft = converse(service, USER_A, PRODUCT_FLOW)
    goal_id = str(uuid.uuid4())
    service.mark_confirmed(draft.session_id, goal_id, draft.revision)
    service.mark_confirmed(draft.session_id, goal_id, draft.revision)
    with pytest.raises(Conflict):
        service.mark_confirmed(draft.session_id, str(uuid.uuid4()), draft.revision)
    after = service.get_session(USER_A, draft.session_id)
    assert after.state == "confirmed" and after.ready_for_confirmation is False
    with pytest.raises(Conflict):
        service.post_message(USER_A, draft.session_id, "actually make it 500", draft.revision)
    with pytest.raises(Conflict):
        service.get_candidate(draft.session_id, draft.revision)


def test_turn_limit_abandons_session(make_service):
    svc = make_service(max_user_turns=2)
    draft = svc.create_session(USER_A, "I want to buy an RTX 5070")
    draft = svc.post_message(USER_A, draft.session_id, "600 euros", draft.revision)
    with pytest.raises(Conflict, match="turn limit"):
        svc.post_message(USER_A, draft.session_id, "yes", draft.revision)
    assert svc.get_session(USER_A, draft.session_id).state == "abandoned"


def test_expired_session_reads_as_abandoned(repo, make_service, goal_draft_validator):
    from datetime import timedelta

    svc = make_service()
    draft = svc.create_session(USER_A, "I want to buy an RTX 5070")
    real_clock = repo.clock
    repo.clock = lambda: real_clock() + timedelta(days=2)
    try:
        expired = svc.get_session(USER_A, draft.session_id)
        goal_draft_validator.validate(expired.model_dump())
        assert expired.state == "abandoned" and not expired.ready_for_confirmation
        with pytest.raises(Conflict):
            svc.post_message(USER_A, draft.session_id, "yes", draft.revision)
        with pytest.raises(Conflict):
            svc.get_candidate(draft.session_id, draft.revision)
    finally:
        repo.clock = real_clock


def test_mark_confirmed_unknown_session_is_409_not_404(service):
    # ICP-01 mark-confirmed lists only 204 / 403 / 409
    with pytest.raises(Conflict):
        service.mark_confirmed(str(uuid.uuid4()), str(uuid.uuid4()), 1)


# ---------------------------------------------------------------- authentication


def test_token_parsing_ignores_weak_or_malformed_entries():
    assert parse_service_tokens(f"gateway={GW}, weak=short, junk, goal-management={GM}") == {
        "gateway": GW, "goal-management": GM}


def test_user_caller_requires_gateway_token_and_uuid():
    auth = Authenticator(TOKENS)
    assert auth.user_caller(f"Bearer {GW}", USER_A).user_id == USER_A
    assert auth.user_caller(f"bearer  {GW} ", USER_A).user_id == USER_A
    for bad in (None, "", GW, f"Basic {GW}", "Bearer wrong-token-0123456789", "Bearer"):
        with pytest.raises(AuthError):
            auth.user_caller(bad, USER_A)
    with pytest.raises(AuthError):
        auth.user_caller(f"Bearer {GW}", "not-a-uuid")
    with pytest.raises(Forbidden):  # a service token cannot impersonate users
        auth.user_caller(f"Bearer {GM}", USER_A)


def test_internal_routes_only_for_goal_management():
    auth = Authenticator(TOKENS)
    assert auth.internal_caller(f"Bearer {GM}").service == "goal-management"
    # ICP-01 lists only 403 for identity failures on internal routes
    for bad in (f"Bearer {GW}", f"Bearer {DM}", None, "Bearer nope-nope-nope-nope", GM):
        with pytest.raises(Forbidden):
            auth.internal_caller(bad)
