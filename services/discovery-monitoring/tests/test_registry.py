from uuid import uuid4

import pytest

from goalpulse_discovery.context_provider import ProviderError
from goalpulse_discovery.registry import canonical_url

PUBLIC = lambda _: ["93.184.216.34"]


def test_tracking_removed_but_variant_and_repeated_selectors_retained():
    assert canonical_url("https://EXAMPLE.com/item?variant=blue&utm_source=ad&size=M&size=L#x", PUBLIC) == "https://example.com/item?variant=blue&size=M&size=L"
    assert canonical_url("https://example.com/item?variant=blue", PUBLIC) != canonical_url("https://example.com/item?variant=red", PUBLIC)


def test_two_goal_urls_share_one_canonical_source():
    assert canonical_url("https://example.com/item?utm_source=one", PUBLIC) == canonical_url("https://example.com/item?fbclid=two", PUBLIC)


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.3", "::1"])
def test_registry_blocks_unsafe_initial_and_redirect_targets(address):
    with pytest.raises(ProviderError):
        canonical_url("https://example.com/item", lambda _: [address])
    # A caller following redirects must validate each destination before its fetch.
    with pytest.raises(ProviderError):
        canonical_url("https://localhost/redirect", PUBLIC)
