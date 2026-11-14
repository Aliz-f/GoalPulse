"""Regression policy: reject named credentials, preserve product selectors."""
import sys
from pathlib import Path
import pytest
SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from notification.fingerprint import public_url


@pytest.mark.parametrize('suffix', [
    '?access_token=FIXTURE', '?ACCESS-TOKEN=FIXTURE', '?api_key=FIXTURE',
    '?client_secret=FIXTURE', '?password=FIXTURE', '?payment_token=FIXTURE',
    '?X-Amz-Credential=FIXTURE&X-Amz-Signature=FIXTURE',
    '?%61ccess%5Ftoken=FIXTURE', '?%2561ccess_token=FIXTURE',
    '#access_token=FIXTURE', '#/callback?access_token=FIXTURE',
    '?anything=Bearer%20FIXTURE', '?variant=red&access_token=FIXTURE', '?api%FFkey=FIXTURE',
])
def test_credential_parameter_url_is_rejected_without_redaction(suffix):
    assert public_url('https://example.com/item' + suffix) is None


@pytest.mark.parametrize('query', ['sku=123&size=L&color=red', 'variant=42',
                                 'product_code=ABC-123', 'color=secret-edition'])
def test_product_variant_query_is_preserved_exactly(query):
    url = 'https://example.com/item?' + query
    assert public_url(url) == url
