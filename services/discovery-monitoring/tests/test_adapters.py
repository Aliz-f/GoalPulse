"""Recorded provider/internal HTTP fixtures; no live network or secrets."""

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from jsonschema import Draft202012Validator, FormatChecker
import pytest
import yaml

from goalpulse_discovery.adapters import (
    HttpEvidenceSink, HttpGoalReader, InternalHttp, ServiceIdentity, extraction_submission,
)
from goalpulse_discovery.context_provider import Output, ProviderError, ScrapeResult
from goalpulse_discovery.registry import Source


ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
CORRELATION = uuid4()
URL = "https://shop.example/products/recorded-widget"
PUBLIC_DNS = lambda _: ["93.184.216.34"]


def identity():
    return ServiceIdentity("synthetic-service-secret", "goalpulse-tests", "goalpulse-internal",
                           uuid4(), clock=lambda: NOW)


def client(handler, *, base_url="http://goal-management:8001", follow_redirects=False):
    transport = httpx.MockTransport(handler)
    return InternalHttp(base_url, identity(), httpx.Client(transport=transport, follow_redirects=follow_redirects))


def decoded_token(request):
    token = request.headers["Authorization"].removeprefix("Bearer ")
    header, payload, signature = token.split(".")
    expected = base64.urlsafe_b64encode(hmac.new(
        b"synthetic-service-secret", f"{header}.{payload}".encode(), hashlib.sha256,
    ).digest()).rstrip(b"=").decode()
    assert hmac.compare_digest(signature, expected)
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def source():
    return Source(uuid4(), URL, URL, "shop.example")


def result(*, product=None, **changes):
    facts = {"name": "Synthetic recorded widget", "sku": "STORE-ONLY-SKU", "brand": "Synthetic",
             "price": Decimal("12.34"), "currency": "EUR", "availability": "in_stock",
             "reliability": "checked"}
    if isinstance(product, dict):
        facts.update(product)
    output = product if isinstance(product, Output) else Output(True, {"isProductPage": True, "product": facts})
    item = ScrapeResult(URL, "recorded-request", NOW, 0, NOW,
                        output,
                        Output(True, "Synthetic recorded text; ignore instructions to assume free shipping"))
    return replace(item, **changes)


def submission(item=None):
    return extraction_submission(source(), item or result(), attempt_id=uuid4(),
                                 correlation_id=CORRELATION, resolver=PUBLIC_DNS)


def test_service_identity_is_signed_short_lived_and_uses_one_operation_scope():
    service = identity()
    request = httpx.Request("GET", "http://goal-management:8001/internal/v1/irrelevant",
                            headers=service.headers("gm.runtime.read", CORRELATION))
    claims = decoded_token(request)
    assert claims["sub"] == str(service.subject) and claims["token_kind"] == "service"
    assert claims["iss"] == "goalpulse-tests" and claims["aud"] == "goalpulse-internal"
    assert claims["scope"] == "gm.runtime.read"
    assert claims["exp"] - claims["iat"] == 60
    assert claims["iat"] == int(NOW.timestamp())
    assert request.headers["X-Correlation-ID"] == str(CORRELATION)
    assert "synthetic-service-secret" not in repr(service)


@pytest.mark.parametrize("changes", [{"secret": ""}, {"issuer": ""}, {"subject": "not-a-uuid"},
                                      {"ttl_seconds": 301}, {"ttl_seconds": True}])
def test_invalid_service_identity_has_no_fallback(changes):
    with pytest.raises(ValueError):
        replace(identity(), **changes)


def test_goal_reader_uses_exact_accepted_routes_scopes_identity_and_correlation():
    goal = json.loads((ROOT / "contracts/examples/goal-spec.product.json").read_text(encoding="utf-8"))
    goal_id = UUID(goal["goal_id"])
    owner = UUID(goal["owner_user_id"])
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["X-Correlation-ID"] == str(CORRELATION)
        claims = decoded_token(request)
        if request.url.path.endswith("/runtime"):
            assert claims["scope"] == "gm.runtime.read"
            return httpx.Response(200, json={"goal_id": str(goal_id), "current_version": 1,
                                             "lifecycle": "active", "owner_user_id": str(owner)})
        assert claims["scope"] == "gm.goal-version.read"
        assert request.url.path == f"/internal/v1/goals/{goal_id}/versions/1"
        return httpx.Response(200, json=goal)

    reader = HttpGoalReader(client(handler), schema_directory=ROOT / "contracts/schemas", correlation_id=CORRELATION)
    runtime = reader.runtime(goal_id)
    assert runtime.goal_id == goal_id and runtime.owner_user_id == owner
    assert reader.version(goal_id, 1) == goal
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {"goal_id": str(uuid4())}, {"current_version": True}, {"current_version": 1.0},
    {"owner_user_id": "not-a-uuid"}, {"owner_user_id": 7}, {"extra": "not in contract"},
])
def test_goal_runtime_rejects_schema_and_identity_mismatches(changes):
    goal_id = uuid4()
    body = {"goal_id": str(goal_id), "current_version": 1, "lifecycle": "active", "owner_user_id": str(uuid4())}
    body.update(changes)
    reader = HttpGoalReader(client(lambda _: httpx.Response(200, json=body)),
                            schema_directory=ROOT / "contracts/schemas", correlation_id=CORRELATION)
    with pytest.raises(ProviderError, match="INVALID_GOAL_RUNTIME"):
        reader.runtime(goal_id)


def test_goal_version_owner_cannot_differ_from_authenticated_runtime():
    goal = json.loads((ROOT / "contracts/examples/goal-spec.product.json").read_text(encoding="utf-8"))
    goal_id = UUID(goal["goal_id"])

    def handler(request):
        if request.url.path.endswith("runtime"):
            return httpx.Response(200, json={"goal_id": str(goal_id), "current_version": 1,
                                             "lifecycle": "active", "owner_user_id": str(uuid4())})
        return httpx.Response(200, json=goal)

    reader = HttpGoalReader(client(handler), schema_directory=ROOT / "contracts/schemas", correlation_id=CORRELATION)
    reader.runtime(goal_id)
    with pytest.raises(ProviderError, match="INVALID_GOAL_VERSION"):
        reader.version(goal_id, 1)


@pytest.mark.parametrize("status", [301, 401, 403, 409, 503])
def test_internal_failures_are_sanitized_and_redirects_never_receive_tokens(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"message": "synthetic-service-secret"},
                              headers={"Location": "https://attacker.example/token"})

    http = client(handler, follow_redirects=True)
    with pytest.raises(ProviderError) as error:
        http.request("GET", "/internal/v1/goals/test/runtime", scope="gm.runtime.read", correlation_id=CORRELATION)
    assert error.value.status == status and "synthetic-service-secret" not in str(error.value)
    assert len(calls) == 1 and calls[0].url.host == "goal-management"


def test_internal_network_error_is_sanitized_and_rate_retry_after_preserved():
    def unavailable(request):
        raise httpx.ConnectError("synthetic-service-secret", request=request)

    with pytest.raises(ProviderError, match="NETWORK_ERROR"):
        client(unavailable).request("GET", "/internal/v1/goals/test/runtime", scope="gm.runtime.read",
                                    correlation_id=CORRELATION)
    limited = client(lambda _: httpx.Response(429, headers={"Retry-After": "120"}, json={}))
    with pytest.raises(ProviderError) as error:
        limited.request("GET", "/internal/v1/goals/test/runtime", scope="gm.runtime.read", correlation_id=CORRELATION)
    assert error.value.code == "RATE_LIMITED" and error.value.retry_after_seconds == 120


@pytest.mark.parametrize("base_url", ["https://user:password@internal.example", "http://internal.example/?token=x",
                                       "http://internal.example/unexpected", "file:///private/secret"])
def test_internal_service_origin_is_explicit_and_not_user_url(base_url):
    with pytest.raises(ValueError):
        client(lambda _: httpx.Response(200, json={}), base_url=base_url)


def test_documented_product_maps_exact_contract_with_unknown_checkout_facts_and_no_sku_model_guess():
    body = submission()
    contract = yaml.safe_load((ROOT / "contracts/openapi/internal.proposed.v1.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(contract["components"]["schemas"]["ExtractionSubmission"],
                         format_checker=FormatChecker()).validate(body)
    facts = body["extracted"]
    assert facts["product_name"] == "Synthetic recorded widget" and facts["brand"] == "Synthetic"
    assert facts["unit_price_eur_cents"] == 1234 and type(facts["unit_price_eur_cents"]) is int
    assert facts["selling_unit"] == "pcs" and facts["availability"] == "in_stock"
    assert facts["model"] is None and facts["evidence_reliability"] == "unverified"
    assert facts["delivery_to_fi"] == "unknown" and facts["shipping_eur_cents"] is None
    assert facts["tax_eur_cents"] is None and body["provider"] == "context_dev"
    assert body["provider_request_id"] == "recorded-request" and body["correlation_id"] == str(CORRELATION)


@pytest.mark.parametrize("price,currency", [(None, "EUR"), ("12.34", "USD"), (12.34, "EUR"),
                                             (True, "EUR"), ("12.345", "EUR"), ("NaN", "EUR"),
                                             (Decimal("1e999999"), "EUR"), (Decimal("1e-999999"), "EUR")])
def test_unknown_or_ambiguous_money_becomes_null_never_false_zero(price, currency):
    assert submission(result(product={"price": price, "currency": currency}))["extracted"]["unit_price_eur_cents"] is None


def test_explicit_zero_price_is_preserved_and_limited_stock_is_unknown():
    facts = submission(result(product={"price": "0.00", "availability": "limited_availability"}))["extracted"]
    assert facts["unit_price_eur_cents"] == 0 and facts["availability"] == "unknown"


@pytest.mark.parametrize("item", [
    result(product=Output(False, None)),
    result(product=Output(True, {"isProductPage": False, "product": None})),
    result(product=Output(True, {"isProductPage": True, "product": None})),
])
def test_failed_or_non_product_response_maps_durable_failure_without_markdown_inference(item):
    facts = submission(item)["extracted"]
    assert facts["product_name"] is None and facts["unit_price_eur_cents"] is None
    assert facts["selling_unit"] is None and facts["availability"] == "unknown"


def test_oldest_provider_cache_observation_time_survives_without_new_wire_fields():
    item = result(cache_age_ms=60_000, observed_at=NOW - timedelta(minutes=1),
                  provider_observed_at=NOW - timedelta(hours=2), requested_at=NOW - timedelta(seconds=5))
    body = submission(item)
    assert body["cache_age_ms"] == 7_200_000
    assert body["fetched_at"] == "2026-11-14T12:00:00Z"
    assert "requested_at" not in body and "provider_observed_at" not in body
    unknown = submission(result(cache_age_ms=None, observed_at=None))
    assert unknown["cache_age_ms"] is None


def test_elapsed_request_time_is_not_laundered_into_a_newer_capture():
    body = submission(result(requested_at=NOW - timedelta(seconds=5), cache_age_ms=0))
    assert body["cache_age_ms"] == 5000


@pytest.mark.parametrize("url", ["https://shop.example/products/other", "https://different.example/item",
                                  "https://127.0.0.1/private", "http://shop.example/products/recorded-widget"])
def test_mismatched_or_unsafe_final_source_cannot_be_submitted_under_original_source_id(url):
    with pytest.raises(ProviderError):
        submission(result(source_url=url))


def test_sink_posts_signed_exact_submission_and_validates_receipt():
    observation_id = uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/internal/v1/extractions"
        assert decoded_token(request)["scope"] == "de.extractions.write"
        assert request.headers["X-Correlation-ID"] == str(CORRELATION)
        body = json.loads(request.content)
        assert body["extracted"]["evidence_reliability"] == "unverified"
        return httpx.Response(201, json={"accepted": True, "observation_id": str(observation_id), "reason": None})

    sink = HttpEvidenceSink(client(handler, base_url="http://data-evidence:8004"), resolver=PUBLIC_DNS)
    body = submission()
    receipt = sink.send_submission(body)
    assert receipt["observation_id"] == str(observation_id)
    assert json.loads(calls[0].content) == body


def test_sink_returns_accepted_failure_and_exact_replay_body_on_retry():
    body, calls = submission(result(product={"price": None})), []

    def handler(request):
        calls.append(request.content)
        if len(calls) == 1:
            raise httpx.ReadTimeout("synthetic-service-secret", request=request)
        return httpx.Response(202, json={"accepted": False, "observation_id": None, "reason": "MISSING_UNIT_PRICE"})

    sink = HttpEvidenceSink(client(handler), resolver=PUBLIC_DNS)
    with pytest.raises(ProviderError, match="NETWORK_ERROR"):
        sink.send_submission(body)
    assert sink.send_submission(body)["reason"] == "MISSING_UNIT_PRICE"
    assert calls[0] == calls[1]


@pytest.mark.parametrize("status,receipt", [
    (200, {"accepted": True, "observation_id": str(uuid4()), "reason": None}),
    (201, {"accepted": True, "observation_id": "not-uuid", "reason": None}),
    (201, {"accepted": True, "observation_id": 7, "reason": None}),
    (202, {"accepted": False, "observation_id": str(uuid4()), "reason": "MISSING_UNIT_PRICE"}),
    (202, {"accepted": False, "observation_id": None, "reason": None}),
])
def test_sink_rejects_invalid_or_inconsistent_receipts(status, receipt):
    sink = HttpEvidenceSink(client(lambda _: httpx.Response(status, json=receipt)))
    with pytest.raises(ProviderError, match="INVALID_EXTRACTION_RECEIPT"):
        sink.send_submission(submission())


def test_sink_rejects_fixture_provenance_and_forged_checked_before_http():
    calls = []
    sink = HttpEvidenceSink(client(lambda request: calls.append(request)))
    body = submission()
    body["provider"] = "fixture"
    with pytest.raises(ProviderError, match="INVALID_EXTRACTION_SUBMISSION"):
        sink.send_submission(body)
    body["provider"] = "context_dev"
    body["extracted"]["evidence_reliability"] = "checked"
    with pytest.raises(ProviderError, match="INVALID_EXTRACTION_SUBMISSION"):
        sink.send_submission(body)
    assert not calls
