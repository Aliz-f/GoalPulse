"""Read-only ICP-01 clients; service-token issuance is supplied by D1's adapter.

No redirects, ambient proxy credentials, or fixture fallback are permitted.
The accepted API does not yet provide a complete goal-to-source lookup.
"""

import json
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .domain import Contracts, Document
from .processing import RetryableUpstreamError, Runtime, Subscription, uuid_string


class InternalReadError(ValueError):
    """Permanent upstream failure, with a fixed code and no response contents."""


class InternalNotFound(InternalReadError):
    """The requested internal resource does not exist."""


class Transport(Protocol):
    def get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    """Bounded standard-library transport for explicitly configured private APIs."""

    def __init__(self, timeout_seconds: float = 5, max_bytes: int = 2_000_000):
        if not 0 < timeout_seconds <= 30 or not 1 <= max_bytes <= 10_000_000:
            raise ValueError('Invalid transport bounds')
        self.timeout, self.max_bytes = timeout_seconds, max_bytes
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        try:
            response = self.opener.open(Request(url, headers=headers), timeout=self.timeout)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            return status, b''
        except (URLError, TimeoutError, OSError):
            raise RetryableUpstreamError('internal_transport_unavailable') from None
        with response:
            try:
                body = response.read(self.max_bytes + 1)
            except (TimeoutError, OSError):
                raise RetryableUpstreamError('internal_transport_unavailable') from None
            if len(body) > self.max_bytes:
                raise InternalReadError('internal_response_too_large')
            return response.status, body


class InternalClient:
    def __init__(self, base_url: str, token: Callable[[str], str], transport: Transport):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
            raise ValueError('Configure an explicit internal service origin')
        self.base_url, self.token, self.transport = base_url.rstrip('/'), token, transport

    def get(self, path: str, scope: str, correlation_id: str) -> Document:
        uuid_string(correlation_id)
        bearer = self.token(scope)
        if not isinstance(bearer, str) or not bearer or any(c.isspace() for c in bearer):
            raise InternalReadError('invalid_service_token')
        status, body = self.transport.get(self.base_url + path, {
            'Authorization': 'Bearer ' + bearer,
            'X-Correlation-ID': correlation_id, 'Accept': 'application/json',
        })
        if status in {408, 429, 500, 502, 503, 504}:
            raise RetryableUpstreamError('internal_read_unavailable')
        if status == 404:
            raise InternalNotFound('internal_resource_not_found')
        if status != 200:
            raise InternalReadError('internal_read_rejected')
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise InternalReadError('invalid_internal_json') from None
        if not isinstance(value, dict):
            raise InternalReadError('invalid_internal_object')
        return value


class GoalHttpReader:
    def __init__(self, client: InternalClient, contracts: Contracts):
        self.client, self.contracts = client, contracts

    def runtime(self, goal_id: str, correlation_id: str) -> Runtime:
        uuid_string(goal_id)
        row = self.client.get(f'/internal/v1/goals/{goal_id}/runtime',
                              'gm.runtime.read', correlation_id)
        if set(row) != {'goal_id', 'owner_user_id', 'current_version', 'lifecycle'}:
            raise InternalReadError('invalid_goal_runtime')
        runtime = Runtime(**row)
        if runtime.goal_id != goal_id:
            raise InternalReadError('goal_identity_mismatch')
        return runtime

    def version(self, subscription: Subscription, correlation_id: str) -> Document:
        row = self.client.get(
            f'/internal/v1/goals/{subscription.goal_id}/versions/{subscription.goal_version}',
            'gm.goal-version.read', correlation_id)
        self.contracts.validate('goal-spec', row)
        if (row['goal_id'], row['version']) != (subscription.goal_id, subscription.goal_version):
            raise InternalReadError('goal_version_mismatch')
        return row


class SubscriptionHttpReader:
    """Only the approved source→goals operation; no fabricated reverse route."""

    def __init__(self, client: InternalClient, max_entries: int = 1000):
        if type(max_entries) is not int or not 1 <= max_entries <= 10000:
            raise ValueError('Invalid subscription bound')
        self.client, self.max_entries = client, max_entries

    def for_source(self, source_id: str, correlation_id: str) -> list[Subscription]:
        uuid_string(source_id)
        row = self.client.get(f'/internal/v1/sources/{source_id}/subscriptions',
                              'dm.sources.read', correlation_id)
        if (set(row) != {'source_id', 'subscriptions'} or row['source_id'] != source_id
                or not isinstance(row['subscriptions'], list)
                or len(row['subscriptions']) > self.max_entries):
            raise InternalReadError('invalid_source_subscriptions')
        result = []
        for entry in row['subscriptions']:
            if not isinstance(entry, dict) or set(entry) != {'goal_id', 'goal_version'}:
                raise InternalReadError('invalid_subscription')
            result.append(Subscription(**entry))
        return result


class EvidenceHttpReader:
    def __init__(self, client: InternalClient, contracts: Contracts, *, profile: str):
        if profile not in {'test', 'dev', 'demo'}:
            raise ValueError('Invalid runtime profile')
        self.client, self.contracts, self.profile = client, contracts, profile

    def _validate(self, row: Document) -> None:
        self.contracts.validate('observation', row)
        if self.profile == 'demo' and (row['provider'] == 'fixture'
                                      or row['extraction_method'] == 'fixture'):
            raise InternalReadError('fixture_provenance_forbidden')

    def observation(self, observation_id: str, correlation_id: str) -> Document:
        uuid_string(observation_id)
        row = self.client.get(f'/internal/v1/observations/{observation_id}',
                              'de.observations.read', correlation_id)
        self._validate(row)
        if row['observation_id'] != observation_id:
            raise InternalReadError('observation_identity_mismatch')
        return row

    def latest_for_source(self, source_id: str, correlation_id: str) -> list[Document]:
        uuid_string(source_id)
        # The accepted operation promises newest first; only the latest is needed.
        row = self.client.get('/internal/v1/observations?' + urlencode({
            'source_id': source_id, 'limit': 1}), 'de.observations.read', correlation_id)
        rows = row.get('observations')
        if not isinstance(rows, list) or len(rows) > 1:
            raise InternalReadError('invalid_observation_list')
        for observation in rows:
            self._validate(observation)
            if observation['source_id'] != source_id:
                raise InternalReadError('source_identity_mismatch')
        return rows
