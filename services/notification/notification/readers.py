"""Authenticated, bounded ICP-01 readers owned entirely by Notification."""

import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from .consumer import RetryableReadError, Runtime
from .contracts import Contracts, Document


class InternalReadError(ValueError):
    """Fixed diagnostic codes only; never include upstream bodies or tokens."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    def __init__(self, *, timeout_seconds: float = 5, max_bytes: int = 2_000_000):
        if not 0 < timeout_seconds <= 30 or not 1 <= max_bytes <= 10_000_000:
            raise ValueError('Invalid HTTP bounds')
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
            raise RetryableReadError('internal_transport_unavailable') from None
        with response:
            try:
                body = response.read(self.max_bytes + 1)
            except (TimeoutError, OSError):
                raise RetryableReadError('internal_transport_unavailable') from None
            if len(body) > self.max_bytes:
                raise InternalReadError('internal_response_too_large')
            return response.status, body


class InternalClient:
    def __init__(self, origin: str, tokens: Callable[[str], str], transport):
        url = urlsplit(origin)
        if (url.scheme not in {'http', 'https'} or not url.hostname
                or url.username is not None or url.password is not None
                or url.path not in {'', '/'} or url.query or url.fragment):
            raise ValueError('Explicit internal origin required')
        self.origin, self.tokens, self.transport = origin.rstrip('/'), tokens, transport

    def get(self, path: str, scope: str, correlation: str) -> Document:
        UUID(correlation)
        token = self.tokens(scope)
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise InternalReadError('invalid_service_token')
        status, body = self.transport.get(self.origin + path, {
            'Authorization': 'Bearer ' + token,
            'X-Correlation-ID': correlation, 'Accept': 'application/json',
        })
        if status in {408, 429, 500, 502, 503, 504}:
            raise RetryableReadError('internal_read_unavailable')
        if status != 200:
            raise InternalReadError('internal_read_rejected')
        try:
            row = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise InternalReadError('invalid_internal_json') from None
        if not isinstance(row, dict):
            raise InternalReadError('invalid_internal_object')
        return row


class GoalHttpReader:
    def __init__(self, client: InternalClient, contracts: Contracts):
        self.client, self.contracts = client, contracts

    def runtime(self, goal_id: str, correlation: str) -> Runtime:
        UUID(goal_id)
        row = self.client.get(f'/internal/v1/goals/{goal_id}/runtime', 'gm.runtime.read', correlation)
        if set(row) != {'goal_id', 'owner_user_id', 'current_version', 'lifecycle'}:
            raise InternalReadError('invalid_runtime')
        result = Runtime(**row)
        if result.goal_id != goal_id:
            raise InternalReadError('goal_identity_mismatch')
        return result

    def version(self, goal_id: str, version: int, correlation: str) -> Document:
        UUID(goal_id)
        if type(version) is not int or version < 1:
            raise ValueError('Invalid goal version')
        row = self.client.get(f'/internal/v1/goals/{goal_id}/versions/{version}',
                              'gm.goal-version.read', correlation)
        self.contracts.validate('goal-spec', row)
        if (row['goal_id'], row['version']) != (goal_id, version):
            raise InternalReadError('goal_version_mismatch')
        return row


class EvaluationHttpReader:
    def __init__(self, client: InternalClient, contracts: Contracts, *, scope: str):
        # D1/GE must provide the operation scope; no guessed scope is minted.
        if not isinstance(scope, str) or not scope or any(c.isspace() for c in scope):
            raise ValueError('Approved evaluation read scope required')
        self.client, self.contracts, self.scope = client, contracts, scope

    def get(self, evaluation_id: str, correlation: str) -> Document:
        UUID(evaluation_id)
        row = self.client.get(f'/internal/v1/evaluations/{evaluation_id}', self.scope, correlation)
        self.contracts.validate('goal-evaluation', row)
        if row['evaluation_id'] != evaluation_id:
            raise InternalReadError('evaluation_identity_mismatch')
        return row


class EvidenceHttpReader:
    def __init__(self, client: InternalClient, contracts: Contracts, *, profile: str):
        if profile not in {'test', 'dev', 'demo'}:
            raise ValueError('Explicit runtime profile required')
        self.client, self.contracts, self.profile = client, contracts, profile

    def _validate(self, row: Document) -> None:
        self.contracts.validate('observation', row)
        if self.profile == 'demo' and (row['provider'] == 'fixture' or row['extraction_method'] == 'fixture'):
            raise InternalReadError('fixture_provenance_forbidden')

    def get(self, observation_id: str, correlation: str) -> Document:
        UUID(observation_id)
        row = self.client.get(f'/internal/v1/observations/{observation_id}', 'de.observations.read', correlation)
        self._validate(row)
        if row['observation_id'] != observation_id:
            raise InternalReadError('observation_identity_mismatch')
        return row

    def latest(self, source_id: str, correlation: str) -> list[Document]:
        UUID(source_id)
        row = self.client.get('/internal/v1/observations?' + urlencode({'source_id': source_id, 'limit': 1}),
                              'de.observations.read', correlation)
        observations = row.get('observations')
        if not isinstance(observations, list) or len(observations) > 1:
            raise InternalReadError('invalid_observation_list')
        for observation in observations:
            self._validate(observation)
            if observation['source_id'] != source_id:
                raise InternalReadError('source_identity_mismatch')
        return observations
