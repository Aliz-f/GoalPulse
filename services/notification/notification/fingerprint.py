"""Local copy of the D4 offer policy; compatibility is pinned by a golden test.

Exclude capture IDs/times; keep goal/version, criteria and normalized offer
snapshot. No additional public field or shared cross-service module is required.
"""
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
from .url_policy import has_credentials
from .contracts import Document


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def public_url(value: str) -> str | None:
    """Reject unsafe literal/link destinations; never fetch or resolve the URL.

    DNS/redirect SSRF controls belong to D3 fetchers. This is a display-link
    filter, not proof that a hostname's DNS resolves to a public destination.
    """
    try:
        if has_credentials(value):
            return None
        url = urlsplit(value)
        host = (url.hostname or '').lower().rstrip('.')
        if (url.scheme not in {'http', 'https'} or not host or url.username is not None or
            url.password is not None or any(ord(c) < 33 for c in value) or '\\' in value):
            return None
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
            return None
        if '%' in host:
            return None
        if host in {'metadata.google.internal', 'metadata.google.com'}:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if all(part.isdigit() for part in host.split('.')):
                return None  # reject ambiguous legacy IPv4 spellings too
        authority = f'[{host}]' if ':' in host else host
        if url.port is not None:
            authority += f':{url.port}'
        return urlunsplit((url.scheme.lower(), authority, url.path, url.query, ''))
    except ValueError:
        return None


def offer_snapshot(observation: Document) -> Document:
    url = public_url(observation['source_url'])
    if url is None:
        raise ValueError('Unsafe evidence source link')
    snapshot = {key: observation.get(key) for key in (
        'product_name', 'brand', 'model', 'selling_unit', 'unit_price_eur_cents',
        'availability', 'delivery_to_fi', 'shipping_eur_cents', 'tax_eur_cents')}
    for key in ('product_name', 'brand', 'model'):
        if isinstance(snapshot[key], str):
            snapshot[key] = ' '.join(snapshot[key].casefold().split())
    snapshot['source_url'] = url
    return snapshot


def same_offer_capture(original: Document, latest: Document) -> bool:
    """A capture ID is provenance, not a change in the verified offer's facts."""
    if (original['source_id'] != latest['source_id'] or
        original['evidence']['reliability'] != 'checked' or latest['evidence']['reliability'] != 'checked'):
        return False
    try:
        return offer_snapshot(original) == offer_snapshot(latest)
    except ValueError:
        return False


def offer_fingerprint(goal: Document, observations: list[Document]) -> str:
    """Phase 1 compatibility key, retained for existing stored notifications."""
    snapshots = [offer_snapshot(observation) for observation in observations]
    payload = dict(goal_id=goal['goal_id'], goal_version=goal['version'],
                   criteria=goal['criteria'], offers=sorted(snapshots, key=canonical))
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def business_fingerprint(goal: Document, observations: list[Document]) -> str:
    """ADR-0003 P0 product business key, independent of listing/capture IDs.

    The exact-model goal query is the immutable selected product key; formatting
    follows the existing evaluator's case/whitespace normalization. v1 cannot
    expose observed product condition, so its key component stays 'unknown'.
    This marker is never evidence that a required condition was satisfied.
    P1 basket canonicalization remains on its existing compatibility policy.
    """
    if goal['goal_type'] != 'product_purchase':
        return offer_fingerprint(goal, observations)
    if len(observations) != 1 or goal['criteria']['quantity'] != 1:
        raise ValueError('P0 business key requires one product and one checked offer')
    observation = observations[0]
    url = public_url(observation['source_url'])
    if url is None:
        raise ValueError('Unsafe evidence source link')
    price, shipping, tax = (observation[k] for k in
                            ('unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents'))
    if any(type(amount) is not int or amount < 0 for amount in (price, shipping, tax)):
        raise ValueError('Business key requires known integer payable components')
    product_key = ' '.join(goal['criteria']['product']['query'].casefold().split())
    if not product_key:
        raise ValueError('Empty canonical product key')
    # Escape the delimiter and the escape marker; preserve ordinary keys.
    product_key = product_key.replace('%', '%25').replace('|', '%7C')
    fields = [str(UUID(goal['goal_id'])), str(goal['version']), urlsplit(url).hostname,
              product_key, 'EUR', str(price + shipping + tax), str(shipping), str(tax),
              'unknown', goal['criteria']['delivery_country']]
    return hashlib.sha256('|'.join(fields).encode('utf-8')).hexdigest()
