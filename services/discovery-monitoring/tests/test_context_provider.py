"""Synthetic provider responses only; no network or credentials are used."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from goalpulse_discovery.context_provider import (
    ContextProvider, HttpResponse, ProviderError, public_https_url,
)
from goalpulse_discovery.probe import coverage

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
PUBLIC_DNS = lambda host: ["93.184.216.34"]


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(data, status=200, headers=None):
    return HttpResponse(status, headers or {}, json.dumps(data).encode())


def provider(*responses, **kwargs):
    transport = FakeTransport(*responses)
    sleeps = []
    client = ContextProvider("synthetic_test_key", transport=transport, resolver=PUBLIC_DNS,
                             clock=lambda: NOW, sleep=sleeps.append, **kwargs)
    return client, transport, sleeps


def scrape_payload(**updates):
    return {"url": "https://example.com/item", "request_id": "fixture-request",
            "product": {"requested": True, "success": True,
                        "data": {"isProductPage": True, "product": {
                            "name": "Synthetic fixture", "price": "3.00", "currency": "EUR"}}},
            "markdown": {"requested": True, "success": True, "data": "Synthetic fixture"},
            "cache_metadata": {"status": "hit", "age_ms": 60_000}, **updates}


def test_request_shapes_authentication_and_timeout():
    client, transport, _ = provider(response({"results": []}), response(scrape_payload()))
    assert client.search("synthetic product", limit=2)["results"] == []
    client.scrape("https://example.com/item#tracking")
    first, second = transport.calls
    assert first[:2] == ("POST", "/v1/web/search")
    assert json.loads(first[2]["body"]) == {"query": "synthetic product", "numResults": 2}
    assert second[:2] == ("POST", "/v1/web/scrape")
    assert json.loads(second[2]["body"])["maxAgeMs"] == 0
    assert json.loads(second[2]["body"])["url"] == "https://example.com/item"
    assert second[2]["headers"]["Authorization"] == "Bearer synthetic_test_key"
    assert second[2]["timeout"] == 30
    assert "synthetic_test_key" not in repr(client)


def test_cached_output_is_never_relabelled_fresh():
    client, _, _ = provider(response(scrape_payload()))
    result = client.scrape("https://example.com/item")
    assert result.observed_at == NOW - timedelta(minutes=1)
    assert result.fetched_at == NOW
    assert result.request_id == "fixture-request"


@pytest.mark.parametrize("cache", [None, {}, {"age_ms": -1}, {"age_ms": True}, {"age_ms": "0"}])
def test_missing_or_invalid_cache_age_stays_unknown(cache):
    client, _, _ = provider(response(scrape_payload(cache_metadata=cache)))
    result = client.scrape("https://example.com/item")
    assert result.cache_age_ms is None
    assert result.observed_at is None


def test_http_200_with_failed_product_does_not_expose_data_as_success():
    client, _, _ = provider(response(scrape_payload(product={"requested": True, "success": False,
                                                            "data": {"price": "invented"}})))
    result = client.scrape("https://example.com/item")
    assert result.product.success is False
    assert result.product.data is None
    assert result.markdown.success
    assert coverage(result)["price_present"] is False


def test_429_respects_retry_after_then_succeeds():
    client, transport, sleeps = provider(response({}, 429, {"Retry-After": "7"}),
                                         response({"results": []}))
    client.search("synthetic product")
    assert sleeps == [7]
    assert len(transport.calls) == 2


def test_retry_after_date_and_excessive_delay():
    client, _, sleeps = provider(response({}, 503, {"retry-after": "Sat, 14 Nov 2026 12:00:05 GMT"}),
                                 response({"results": []}))
    client.search("synthetic product")
    assert sleeps == [5]
    client, transport, sleeps = provider(response({}, 429, {"Retry-After": "120"}))
    with pytest.raises(ProviderError) as error:
        client.search("synthetic product")
    assert error.value.retry_after_seconds == 120
    assert len(transport.calls) == 1 and not sleeps


def test_network_retries_are_bounded_and_sanitized():
    client, transport, sleeps = provider(*[TimeoutError("secret echoed") for _ in range(3)])
    with pytest.raises(ProviderError) as error:
        client.search("synthetic product")
    assert str(error.value) == "NETWORK_ERROR"
    assert sleeps == [1, 2]
    assert len(transport.calls) == 3


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 302])
def test_permanent_errors_and_redirects_are_not_retried_or_echoed(status):
    client, transport, sleeps = provider(response({"message": "synthetic_test_key"}, status))
    with pytest.raises(ProviderError) as error:
        client.search("synthetic product")
    assert "synthetic_test_key" not in str(error.value)
    assert len(transport.calls) == 1 and not sleeps


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"price": NaN}', b"\xff"])
def test_invalid_json_rejected_without_retry(body):
    client, transport, _ = provider(HttpResponse(200, {}, body))
    with pytest.raises(ProviderError, match="INVALID_RESPONSE"):
        client.search("synthetic product")
    assert len(transport.calls) == 1


def test_create_monitor_secret_is_private_and_baseline_is_distinct():
    client, transport, _ = provider(response({"id": "mon_fixture", "initial_run_id": "run_baseline",
                                             "webhook": {"secret": "fixture_webhook_secret"}}, 201),
                                    response({"id": "run_baseline", "run_type": "baseline",
                                              "baseline_created": True, "change_detected": False}))
    monitor = client.create_monitor(name="Synthetic monitor", url="https://example.com/item",
                                    instructions="Report price changes", webhook_url="https://example.com/hook")
    assert monitor.webhook_secret == "fixture_webhook_secret"
    assert "fixture_webhook_secret" not in repr(monitor)
    assert monitor.initial_run_id == "run_baseline"
    run = client.fetch_run(monitor.monitor_id, monitor.initial_run_id)
    assert run["change_detected"] is False
    assert transport.calls[1][:2] == ("GET", "/v1/monitors/mon_fixture/runs/run_baseline")


def test_ambiguous_monitor_creation_is_not_automatically_retried():
    client, transport, sleeps = provider(TimeoutError())
    with pytest.raises(ProviderError):
        client.create_monitor(name="Fixture", url="https://example.com/item")
    assert len(transport.calls) == 1 and not sleeps


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost", "https://user:password@example.com",
                                  "https://example.com:8080", "https://example.com\\@127.0.0.1",
                                  "https://example.com/\nInjected"])
def test_unsafe_url_is_rejected_before_provider_request(url):
    client, transport, _ = provider()
    with pytest.raises(ProviderError, match="UNSAFE_TARGET"):
        client.scrape(url)
    assert not transport.calls


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "100.64.0.1"])
def test_private_or_mixed_dns_answers_are_rejected(address):
    with pytest.raises(ProviderError, match="UNSAFE_TARGET"):
        public_https_url("https://example.com/item", lambda _: ["93.184.216.34", address])


def test_unsafe_provider_final_url_is_not_accepted_as_evidence():
    client, _, _ = provider(response(scrape_payload(url="https://localhost/private")))
    with pytest.raises(ProviderError, match="UNSAFE_TARGET"):
        client.scrape("https://example.com/item")


def test_provider_values_are_not_printed_by_probe_summary():
    client, _, _ = provider(response(scrape_payload()))
    result = client.scrape("https://example.com/item")
    report = coverage(result)
    assert report["product_name_present"] and report["price_present"]
    assert "Synthetic fixture" not in json.dumps(report)
    assert "3.00" not in json.dumps(report)


def test_non_product_success_has_no_product_coverage():
    client, _, _ = provider(response(scrape_payload(product={
        "requested": True, "success": True, "data": {"isProductPage": False, "product": None}})))
    report = coverage(client.scrape("https://example.com/item"))
    assert report["product_success"]
    assert not report["price_present"] and not report["product_name_present"]


def test_probe_without_key_reports_not_run(monkeypatch, capsys, tmp_path):
    from goalpulse_discovery import config
    from goalpulse_discovery.probe import main

    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    monkeypatch.setattr(config, "LOCAL_ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr("sys.argv", ["probe", "https://example.com/item"])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "NOT RUN"
