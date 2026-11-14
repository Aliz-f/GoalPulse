"""D2-GI-04: public session semantics, GM-only candidate/mark-confirmed, and auth (service layer)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import pytest
from conftest import USER_A, USER_B, converse

from goal_intelligence.auth import (
    Authenticator,
    AuthError,
    Forbidden,
    GATEWAY_SESSION_SCOPE,
    GM_CANDIDATE_SCOPE,
    GM_CONFIRM_SCOPE,
    ServiceJwtVerifier,
)
from goal_intelligence.service import Conflict, NotFound
from test_gi_compiler import PRODUCT_FLOW

JWT_SECRET = "goal-intelligence-service-jwt-secret-32-chars"
JWT_ISSUER = "goalpulse-internal-dev"
JWT_AUDIENCE = "goalpulse-internal"
GATEWAY_SUBJECT = "ae000000-0000-4000-8000-000000000010"
GM_SUBJECT = "ae000000-0000-4000-8000-000000000011"
DM_SUBJECT = "ae000000-0000-4000-8000-000000000012"


def service_jwt(
    scope: str,
    *,
    subject: str = GM_SUBJECT,
    issuer: str = JWT_ISSUER,
    audience: str = JWT_AUDIENCE,
    expires: int = 4102444800,
    secret: str = JWT_SECRET,
) -> str:
    encode = lambda value: base64.urlsafe_b64encode(  # noqa: E731 - compact deterministic test JWT helper
        json.dumps(value, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    header = encode({"alg": "HS256", "typ": "JWT"})
    payload = encode({
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "token_kind": "service",
        "scope": scope,
        "exp": expires,
    })
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.{signature}"


GW = service_jwt(GATEWAY_SESSION_SCOPE, subject=GATEWAY_SUBJECT)
GM = service_jwt(f"{GM_CANDIDATE_SCOPE} {GM_CONFIRM_SCOPE}")
DM = service_jwt("dm.extraction.write", subject=DM_SUBJECT)


def authenticator() -> Authenticator:
    return Authenticator(ServiceJwtVerifier(secret=JWT_SECRET, issuer=JWT_ISSUER, audience=JWT_AUDIENCE))


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


def test_user_caller_requires_valid_scoped_gateway_jwt_and_uuid():
    auth = authenticator()
    assert auth.user_caller(f"Bearer {GW}", USER_A).user_id == USER_A
    assert auth.user_caller(f"bearer  {GW} ", USER_A).user_id == USER_A
    for bad in (
        None,
        "",
        GW,
        f"Basic {GW}",
        "Bearer wrong-token",
        "Bearer",
        f"Bearer {service_jwt(GATEWAY_SESSION_SCOPE, issuer='wrong-issuer')}",
        f"Bearer {service_jwt(GATEWAY_SESSION_SCOPE, audience='wrong-audience')}",
        f"Bearer {service_jwt(GATEWAY_SESSION_SCOPE, expires=1)}",
        f"Bearer {service_jwt(GATEWAY_SESSION_SCOPE, subject='not-a-uuid')}",
        f"Bearer {service_jwt(GATEWAY_SESSION_SCOPE, secret='different-signing-secret-that-is-long-enough')}",
    ):
        with pytest.raises(AuthError):
            auth.user_caller(bad, USER_A)
    with pytest.raises(AuthError):
        auth.user_caller(f"Bearer {GW}", "not-a-uuid")
    with pytest.raises(Forbidden):  # a service token without the Gateway scope cannot impersonate users
        auth.user_caller(f"Bearer {GM}", USER_A)


def test_internal_routes_only_for_goal_management():
    auth = authenticator()
    assert auth.internal_caller(f"Bearer {GM}", GM_CANDIDATE_SCOPE).subject == GM_SUBJECT
    assert auth.internal_caller(f"Bearer {GM}", GM_CONFIRM_SCOPE).subject == GM_SUBJECT
    candidate_only = service_jwt(GM_CANDIDATE_SCOPE)
    with pytest.raises(Forbidden):
        auth.internal_caller(f"Bearer {candidate_only}", GM_CONFIRM_SCOPE)
    # ICP-01 lists only 403 for identity failures on internal routes
    for bad in (f"Bearer {GW}", f"Bearer {DM}", None, "Bearer nope-nope-nope-nope", GM):
        with pytest.raises(Forbidden):
            auth.internal_caller(bad, GM_CANDIDATE_SCOPE)
