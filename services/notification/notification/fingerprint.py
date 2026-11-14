"""Local copy of the D4 offer policy; compatibility is pinned by a golden test.

Exclude capture IDs/times; keep goal/version, criteria and normalized offer
snapshot. No additional public field or shared cross-service module is required.
"""
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit
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
    snapshots = [offer_snapshot(observation) for observation in observations]
    payload = dict(goal_id=goal['goal_id'], goal_version=goal['version'],
                   criteria=goal['criteria'], offers=sorted(snapshots, key=canonical))
    return hashlib.sha256(canonical(payload).encode()).hexdigest()
