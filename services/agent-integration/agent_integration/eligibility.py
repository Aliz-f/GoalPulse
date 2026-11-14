"""Pure handoff preconditions, not a package serializer or permission to act."""
import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from .url_policy import has_credentials

Document = dict[str, Any]
PRODUCT_CODES = {'identity_match', 'condition', 'availability', 'delivery', 'seller_policy',
                 'freshness', 'reliability', 'tax_known', 'shipping_known', 'total_within_budget'}
PROCUREMENT_CODES = {'full_basket_coverage', 'units_compatible', 'quality', 'availability',
                     'delivery', 'seller_limit', 'freshness', 'reliability',
                     'shipping_known_by_seller', 'tax_known', 'total_within_budget'}


class Contracts:
    def __init__(self, schema_directory: Path):
        self.validators = {name: Draft202012Validator(
            json.loads((schema_directory / f'{name}.v1.schema.json').read_text()),
            format_checker=FormatChecker())
            for name in ('domain-event', 'goal-spec', 'goal-evaluation', 'observation')}

    def validate(self, name: str, value: Document) -> None:
        self.validators[name].validate(value)


@dataclass(frozen=True)
class Runtime:
    """Local adapter value; must originate from an authenticated GM read."""
    goal_id: str
    owner_user_id: str
    current_version: int
    lifecycle: str

    def __post_init__(self):
        UUID(self.goal_id)
        UUID(self.owner_user_id)
        if type(self.current_version) is not int or self.current_version < 1:
            raise ValueError('Invalid runtime version')
        if self.lifecycle not in {'active', 'paused', 'completed', 'cancelled'}:
            raise ValueError('Invalid runtime lifecycle')


@dataclass(frozen=True)
class Eligibility:
    """Local preflight result only; no package or external authorization token."""
    allowed: bool
    reason: str
    expires_at: datetime | None = None

    @property
    def approval_required(self) -> bool:
        return True

    @property
    def auto_execute(self) -> bool:
        return False


def safe_source(value: str) -> bool:
    """Literal display-reference checks, never DNS resolution or URL fetching."""
    try:
        if has_credentials(value):
            return False
        url = urlsplit(value)
        host = (url.hostname or '').lower().rstrip('.')
        _ = url.port  # validate an explicit port without connecting
        if (url.scheme not in {'http', 'https'} or not host or url.username is not None or
            url.password is not None or '\\' in value or any(ord(c) < 33 for c in value) or
            '%' in host or '.' not in host or host in {'metadata.google.com', 'localhost'} or
            host.endswith(('.localhost', '.local', '.internal'))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return not all(part.isdigit() for part in host.split('.'))
    except ValueError:
        return False


class HandoffEligibility:
    """Check immutable input identities, provenance, current state and lifetime.

    Caller must supply fresh authenticated reads, recheck at persistence/read
    time, and use an approved package contract before building anything. GE
    remains the evaluator; this module cannot execute, serialize or send actions.
    """

    def __init__(self, contracts: Contracts):
        self.contracts = contracts

    def check(self, event: Document, goal: Document, evaluation: Document,
              observations: Sequence[Document], runtime: Runtime, now_utc: datetime) -> Eligibility:
        for name, value in (('domain-event', event), ('goal-spec', goal), ('goal-evaluation', evaluation)):
            self.contracts.validate(name, value)
        if now_utc.utcoffset() != timedelta(0):
            raise ValueError('Clock must be timezone-aware UTC')
        if event['event_type'] != 'goal.opportunity_verified.v1':
            raise ValueError('Expected verified opportunity event')
        payload = event['payload']
        if (event['aggregate_id'], payload['goal_id'], payload['goal_version'], payload['evaluation_id']) != (
            goal['goal_id'], goal['goal_id'], goal['version'], evaluation['evaluation_id'],
        ) or (evaluation['goal_id'], evaluation['goal_version']) != (goal['goal_id'], goal['version']):
            raise ValueError('Goal/evaluation/event identity mismatch')
        if not isinstance(runtime, Runtime) or runtime.goal_id != goal['goal_id']:
            raise ValueError('Runtime identity mismatch')
        if (runtime.lifecycle != 'active' or runtime.current_version != goal['version'] or
            runtime.owner_user_id != goal['owner_user_id']):
            return Eligibility(False, 'inactive_obsolete_or_unowned')
        if not evaluation['is_opportunity_verified'] or evaluation['status'] != 'satisfied':
            return Eligibility(False, 'not_verified')
        if datetime.fromisoformat(evaluation['evaluated_at'].replace('Z', '+00:00')) > now_utc:
            return Eligibility(False, 'future_evaluation')
        required = PRODUCT_CODES if goal['goal_type'] == 'product_purchase' else PROCUREMENT_CODES
        codes = [condition['code'] for condition in evaluation['conditions']]
        selected = set(evaluation['candidate_observation_ids'])
        if not required <= set(codes) or len(codes) != len(set(codes)):
            return Eligibility(False, 'incomplete_conditions')
        if not selected or len(selected) > 200 or len(observations) > 200:
            return Eligibility(False, 'missing_or_excessive_evidence')
        if any(not condition['observation_ids'] or not set(condition['observation_ids']) <= selected
               for condition in evaluation['conditions']):
            return Eligibility(False, 'missing_condition_provenance')
        by_id = {}
        for observation in observations:
            self.contracts.validate('observation', observation)
            oid = observation['observation_id']
            if oid in by_id and by_id[oid] != observation:
                raise ValueError('Conflicting immutable observation')
            by_id[oid] = observation
        if not selected <= by_id.keys():
            return Eligibility(False, 'missing_evidence')
        deadlines = []
        for oid in selected:
            o = by_id[oid]
            for key in ('unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents'):
                if o[key] is not None and type(o[key]) is not int:
                    raise ValueError('Money must use integer EUR cents')
            if (o['availability'] != 'in_stock' or o['delivery_to_fi'] != 'yes' or
                o['shipping_eur_cents'] is None or o['tax_eur_cents'] is None or
                o['evidence']['reliability'] != 'checked' or not safe_source(o['source_url'])):
                return Eligibility(False, 'incomplete_or_unsafe_evidence')
            observed = datetime.fromisoformat(o['observed_at'].replace('Z', '+00:00'))
            deadline = observed + timedelta(minutes=goal['monitoring']['max_evidence_age_minutes'])
            if observed > now_utc or deadline <= now_utc:
                return Eligibility(False, 'expired_or_future_evidence')
            deadlines.append(deadline)
        if goal['criteria']['delivery_country'] != 'FI':
            return Eligibility(False, 'unsupported_destination')
        if goal['goal_type'] == 'product_purchase':
            product = goal['criteria']['product']
            if product['condition'] != 'any' or set(product.get('required_attributes', {})) - {'brand', 'model', 'product_name'}:
                return Eligibility(False, 'evidence_contract_gap')
        elif any(item['requirements'] for item in goal['criteria']['items']):
            return Eligibility(False, 'evidence_contract_gap')
        return Eligibility(True, 'preconditions_checked_only', min(deadlines))
