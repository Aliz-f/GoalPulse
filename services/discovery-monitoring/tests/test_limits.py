from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from goalpulse_discovery.context_provider import Output, ProviderError, ScrapeResult
from goalpulse_discovery.limits import GuardedProvider

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def test_quota_or_cooldown_denial_calls_no_provider():
    provider, usage = Mock(), Mock()
    usage.reserve.side_effect = ProviderError("RATE_LIMITED", retry_after_seconds=600)
    guarded = GuardedProvider(provider, usage, clock=lambda: NOW)
    with pytest.raises(ProviderError) as error:
        guarded.scrape("https://example.com/item")
    assert error.value.retry_after_seconds == 600
    provider.scrape.assert_not_called()
    assert guarded.semaphore.acquire(blocking=False)  # No permit leak.


def test_unknown_cost_stays_unknown_and_known_credit_report_is_preserved():
    provider, usage = Mock(), Mock()
    provider.scrape.side_effect = [
        ScrapeResult("https://example.com/item", None, NOW, 0, NOW, Output(False, None), Output(False, None)),
        ScrapeResult("https://example.com/item", None, NOW, 0, NOW, Output(True, {}), Output(True, "fixture"), 2),
    ]
    guarded = GuardedProvider(provider, usage, clock=lambda: NOW)
    guarded.scrape("https://example.com/item")
    assert usage.finish.call_args.kwargs["credits"] is None
    guarded.scrape("https://example.com/item")
    assert usage.finish.call_args.kwargs["credits"] == 2


def test_rate_limit_persists_backoff_without_secret_or_raw_exception():
    provider, usage = Mock(), Mock()
    provider.search.side_effect = ProviderError("RATE_LIMITED", retry_after_seconds=120)
    guarded = GuardedProvider(provider, usage, clock=lambda: NOW)
    with pytest.raises(ProviderError):
        guarded.search("synthetic product")
    assert usage.finish.call_args.kwargs["retry_after"] == 120
    assert usage.finish.call_args.kwargs["error_code"] == "PROVIDER_FAILURE"
    assert usage.finish.call_args.kwargs["credits"] is None


def test_concurrency_limit_fails_promptly_without_reserving_budget():
    provider, usage = Mock(), Mock()
    guarded = GuardedProvider(provider, usage, max_concurrent=1)
    assert guarded.semaphore.acquire(blocking=False)
    with pytest.raises(ProviderError):
        guarded.scrape("https://example.com/item")
    usage.reserve.assert_not_called()


def test_retried_operation_does_not_claim_last_response_is_complete_cost():
    provider, usage = Mock(), Mock()
    provider.scrape.return_value = ScrapeResult("https://example.com/item", None, NOW, 0, NOW,
                                               Output(True, {}), Output(True, "fixture"), 2, 2)
    GuardedProvider(provider, usage, clock=lambda: NOW).scrape("https://example.com/item")
    assert usage.finish.call_args.kwargs["credits"] is None
