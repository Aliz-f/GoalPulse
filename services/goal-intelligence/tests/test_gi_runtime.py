"""R2-D2-01: runtime profiles fail closed, no fixture in demo, no silent provider fallback,
visible provider failure that never leaves a draft confirmable, and secret-free disclosure."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest
from conftest import USER_A, converse

from goal_intelligence.config import ConfigError, Settings, build_extractor, runtime_disclosure, validate_settings
from goal_intelligence.llm.anthropic import AnthropicExtractor
from goal_intelligence.llm.base import ExtractorError, ExtractorMalformedOutput, ExtractorTimeout, ResilientExtractor
from goal_intelligence.llm.fixture import FixtureExtractor
from goal_intelligence.service import RETRY_NOTICE, SUMMARY_APPROVAL_MESSAGE
from test_gi_compiler import PRODUCT_FLOW

KEY = "sk-ant-test-0123456789-never-print-me"


def settings(**overrides) -> Settings:
    base = Settings(
        profile="demo", database_url="sqlite:///unused.db", llm_provider="anthropic", llm_model="test-model",
        anthropic_api_key=KEY, llm_timeout_s=20, llm_max_attempts=3, max_message_chars=2000, max_user_turns=20,
        service_jwt_secret="", service_jwt_issuer="", service_jwt_audience="",
    )
    return replace(base, **overrides)


# ---------------------------------------------------------------- profiles


def test_demo_with_anthropic_is_accepted():
    validate_settings(settings())
    extractor = build_extractor(settings())
    assert isinstance(extractor.inner, AnthropicExtractor) and extractor.model_id == "test-model"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"llm_provider": "fixture"}, "fixture extractor is refused"),
        ({"llm_provider": ""}, "GI_LLM_PROVIDER must be set explicitly"),
        ({"llm_provider": "openai"}, "GI_LLM_PROVIDER must be set explicitly"),
        ({"llm_model": ""}, "GI_LLM_MODEL is required"),
        ({"anthropic_api_key": ""}, "ANTHROPIC_API_KEY is required"),
        ({"profile": ""}, "GOALPULSE_PROFILE must be one of"),
        ({"profile": "prod"}, "GOALPULSE_PROFILE must be one of"),
        ({"llm_max_attempts": 0}, "GI_LLM_MAX_ATTEMPTS"),
    ],
)
def test_invalid_runtime_fails_closed(overrides, fragment):
    with pytest.raises(ConfigError, match=fragment) as info:
        build_extractor(settings(**overrides))
    assert KEY not in str(info.value)


@pytest.mark.parametrize("profile", ["test", "dev"])
def test_fixture_only_when_explicit_outside_demo(profile):
    extractor = build_extractor(settings(profile=profile, llm_provider="fixture", llm_model="", anthropic_api_key=""))
    assert isinstance(extractor.inner, FixtureExtractor)
    with pytest.raises(ConfigError):
        build_extractor(settings(profile=profile, llm_provider=""))


def test_settings_have_no_provider_or_model_defaults(monkeypatch):
    for name in ("GOALPULSE_PROFILE", "GI_LLM_PROVIDER", "GI_LLM_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert (s.profile, s.llm_provider, s.llm_model, s.anthropic_api_key) == ("", "", "", "")
    assert KEY not in repr(settings())  # secrets are excluded from repr


def test_disclosure_names_the_receiver_and_never_secrets():
    info = runtime_disclosure(settings())
    assert info == {
        "profile": "demo", "llm_provider": "anthropic", "llm_model": "test-model",
        "goal_text_sent_to": "Anthropic (Messages API)",
    }
    assert KEY not in json.dumps(info)
    dev = runtime_disclosure(settings(profile="dev", llm_provider="fixture"))
    assert dev["llm_provider"] == "fixture" and "Anthropic" not in dev["goal_text_sent_to"]


# ---------------------------------------------------------------- provider failure


def anthropic_with(status: int, payload: dict | None = None) -> AnthropicExtractor:
    def transport(url, headers, body, timeout):
        return status, json.dumps(payload).encode() if payload is not None else b""

    return AnthropicExtractor(KEY, "test-model", transport=transport)


def timing_out() -> AnthropicExtractor:
    """An Anthropic adapter whose HTTP transport always times out."""

    def transport(url, headers, body, timeout):
        raise ExtractorTimeout("model request timed out")

    return AnthropicExtractor(KEY, "test-model", transport=transport)


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "provider_auth_failed"), (403, "provider_forbidden"), (404, "model_not_found"), (400, "provider_rejected_request")],
)
def test_permanent_provider_errors_have_distinct_codes_and_are_not_retried(status, code):
    ext = ResilientExtractor(anthropic_with(status), max_attempts=3, sleep=lambda s: None)
    from goal_intelligence.domain.extraction import ExtractionRequest

    with pytest.raises(ExtractorError):
        ext.extract(ExtractionRequest(message="I want to buy a Kindle"))
    assert [a.error_code for a in ext.attempts] == [code]


def test_truncated_model_output_is_malformed():
    payload = {"content": [{"type": "text", "text": '{"classification":"product_pur'}], "stop_reason": "max_tokens"}
    from goal_intelligence.domain.extraction import ExtractionRequest

    with pytest.raises(ExtractorMalformedOutput):
        anthropic_with(200, payload).extract(ExtractionRequest(message="x"), timeout_s=1)


def test_provider_outage_never_falls_back_to_fixture(make_service, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("fixture extractor must never be used as a fallback")

    monkeypatch.setattr(FixtureExtractor, "extract", forbidden)
    svc = make_service(timing_out())
    draft = svc.create_session(USER_A, "I want to buy an RTX 5070 for under 600 euros")
    assert draft.next_question.startswith(RETRY_NOTICE)
    assert not draft.ready_for_confirmation and "criteria" not in draft.candidate_goal


def test_provider_failure_withdraws_readiness_instead_of_dropping_a_change(make_service):
    ready = converse(make_service(), USER_A, PRODUCT_FLOW)
    assert ready.ready_for_confirmation
    after = make_service(timing_out()).post_message(USER_A, ready.session_id, "make it 500 euros instead", ready.revision)
    assert after.revision == ready.revision + 1
    assert not after.ready_for_confirmation and after.state == "collecting"
    assert after.missing_fields == ["summary_approval"]
    assert after.next_question.startswith(RETRY_NOTICE)
    assert after.candidate_goal == ready.candidate_goal  # nothing from the failed call applied


@pytest.mark.parametrize("reply", ["deal", "YES!", "Looks good to me.", SUMMARY_APPROVAL_MESSAGE])
def test_explicit_summary_consent_is_deterministic_during_provider_outage(make_service, reply):
    draft = converse(make_service(), USER_A, PRODUCT_FLOW[:-1])
    assert draft.missing_fields == ["summary_approval"]

    unavailable = make_service(timing_out())
    ready = unavailable.post_message(USER_A, draft.session_id, reply, draft.revision)

    assert ready.ready_for_confirmation
    assert ready.state == "ready_for_confirmation"
    assert unavailable.extractor.attempts == []


def test_summary_reply_with_a_change_still_requires_extraction(make_service):
    draft = converse(make_service(), USER_A, PRODUCT_FLOW[:-1])
    after = make_service(timing_out()).post_message(
        USER_A,
        draft.session_id,
        "yes, but change the budget to 500 euros",
        draft.revision,
    )

    assert not after.ready_for_confirmation
    assert after.missing_fields == ["summary_approval"]
    assert after.next_question.startswith(RETRY_NOTICE)


def test_provider_failure_logs_no_goal_text_or_key(make_service, caplog):
    secret_goal = "buy a hearing aid for my mother Liisa, budget 900 euros"
    with caplog.at_level(logging.DEBUG):
        make_service(timing_out()).create_session(USER_A, secret_goal)
    assert "Liisa" not in caplog.text and KEY not in caplog.text
