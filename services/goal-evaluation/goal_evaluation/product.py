"""Deterministic product offers using only facts representable by frozen v1."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Sequence
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from .domain import Contracts, Document

CODES = (
    'identity_match', 'condition', 'availability', 'delivery', 'seller_policy',
    'freshness', 'reliability', 'tax_known', 'shipping_known', 'units_compatible',
    'total_within_budget',
)


def normalized(value: str) -> str:
    """Case/whitespace normalization only; no fuzzy or substring equivalence."""
    return ' '.join(value.casefold().split())


def aggregate(conditions: Sequence[Document]) -> str:
    statuses = {c['status'] for c in conditions}
    if 'unsatisfied' in statuses:
        return 'unsatisfied'
    return 'unknown' if 'unknown' in statuses else 'satisfied'


class ProductPurchaseEvaluator:
    """Pure evaluator. Caller must recheck active/latest runtime before publishing.

    Each observation is one alternative offer, never a partial basket. Shipping
    and additional tax are offer-level amounts applied once for the requested
    quantity. Price is per pcs. Condition=new and unrepresented attributes remain
    unknown; evidence text is never interpreted as structured permission/facts.
    """

    version = 'product-v1.3-phase2'

    def __init__(self, contracts: Contracts):
        self.contracts = contracts

    def evaluate(self, goal: Document, observations: Sequence[Document], now_utc: datetime) -> Document:
        self.contracts.validate('goal-spec', goal)
        if goal['goal_type'] != 'product_purchase':
            raise ValueError('ProductPurchaseEvaluator requires product_purchase')
        if now_utc.utcoffset() != timedelta(0):
            raise ValueError('Evaluation clock must be timezone-aware UTC')
        for key in ('quantity', 'max_total_eur_cents'):
            if type(goal['criteria'][key]) is not int:
                raise ValueError(f'{key} must use an integer representation')
        by_id: dict[str, Document] = {}
        for observation in observations:
            self.contracts.validate('observation', observation)
            for key in ('unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents'):
                if observation[key] is not None and type(observation[key]) is not int:
                    raise ValueError(f'{key} must use integer EUR cents')
            oid = observation['observation_id']
            if oid in by_id and by_id[oid] != observation:
                raise ValueError('Conflicting snapshots for one observation ID')
            by_id[oid] = observation
        offers = []
        for observation in by_id.values():
            conditions, total = self._conditions(goal, observation, now_utc)
            status = aggregate(conditions)
            offers.append(({'satisfied': 0, 'unknown': 1, 'unsatisfied': 2}[status],
                           total is None, total or 0, observation['observation_id'], conditions))
        if offers:
            _, _, _, oid, conditions = min(offers, key=lambda offer: offer[:4])
            candidates = [oid]
        else:
            conditions = [dict(code=code, status='unknown', reason='No observation supplied',
                               observation_ids=[]) for code in CODES]
            candidates = []
        status = aggregate(conditions)
        result = dict(
            goal_id=goal['goal_id'], goal_version=goal['version'],
            evaluated_at=now_utc.isoformat().replace('+00:00', 'Z'), status=status,
            conditions=conditions, candidate_observation_ids=candidates,
            is_opportunity_verified=status == 'satisfied' and bool(candidates),
        )
        # Stable for identical inputs, regardless of alternative-offer ordering.
        identity = json.dumps({'evaluator_version': self.version, 'goal': goal, 'observations': sorted(by_id.values(),
                              key=lambda o: o['observation_id']), 'result': result},
                              sort_keys=True, separators=(',', ':'))
        result['evaluation_id'] = str(uuid5(NAMESPACE_URL, hashlib.sha256(identity.encode()).hexdigest()))
        self.contracts.validate('goal-evaluation', result)
        return result

    def _conditions(
        self, goal: Document, observation: Document, now: datetime,
    ) -> tuple[list[Document], int | None]:
        criteria, conditions = goal['criteria'], []
        product = criteria['product']
        oid = observation['observation_id']

        def add(code: str, status: str, reason: str) -> None:
            conditions.append(dict(code=code, status=status, reason=reason, observation_ids=[oid]))

        model = observation.get('model')
        query = normalized(product['query'])
        exact_names = {normalized(model)} if model and model.strip() else set()
        if exact_names and observation.get('brand'):
            exact_names.add(normalized(observation['brand'] + ' ' + model))
        if query in exact_names:
            identity, reason = 'satisfied', 'Query exactly matches structured model or brand plus model'
        elif product['match_policy'] == 'equivalent_allowed':
            identity, reason = 'unknown', 'No approved equivalence policy for this different model'
        elif not exact_names:
            identity, reason = 'unknown', 'Structured model is missing'
        else:
            identity, reason = 'unsatisfied', 'Structured model does not exactly match query'
        for key, expected in sorted(product.get('required_attributes', {}).items()):
            if key not in {'brand', 'model', 'product_name'} or not observation.get(key):
                if identity != 'unsatisfied':
                    identity = 'unknown'
                reason += f'; required attribute {key} cannot be established from v1'
            elif normalized(observation[key]) != normalized(expected):
                identity = 'unsatisfied'
                reason += f'; required attribute {key} differs'
        add('identity_match', identity, reason)
        add('condition', 'satisfied' if product['condition'] == 'any' else 'unknown',
            'Any condition accepted' if product['condition'] == 'any' else
            'Observation.v1 has no product-condition field; new cannot be established')
        availability = observation['availability']
        if availability == 'out_of_stock':
            availability_status = 'unsatisfied'
            availability_reason = 'Observed availability: out_of_stock'
        elif availability == 'unknown':
            availability_status = 'unknown'
            availability_reason = 'Observed availability is unknown'
        elif criteria['quantity'] == 1:
            availability_status = 'satisfied'
            availability_reason = 'One requested piece is reported in stock'
        else:
            availability_status = 'unknown'
            availability_reason = (
                'Observation.v1 reports in_stock but has no available quantity; '
                f'{criteria["quantity"]} requested pieces cannot be established'
            )
        add('availability', availability_status, availability_reason)
        delivery = observation['delivery_to_fi']
        add('delivery', {'yes': 'satisfied', 'no': 'unsatisfied', 'unknown': 'unknown'}[delivery]
            if criteria['delivery_country'] == 'FI' else 'unknown',
            'FI delivery: ' + delivery if criteria['delivery_country'] == 'FI' else
            'Observation.v1 cannot establish delivery outside FI')
        try:
            url = urlsplit(observation['source_url'])
            host = (url.hostname or '').lower().rstrip('.')
            valid_host = bool(host) and url.username is None and url.password is None
        except ValueError:
            host, valid_host = '', False
        allowed = {d.lower().rstrip('.') for d in criteria.get('allowed_seller_domains', [])}
        blocked = {d.lower().rstrip('.') for d in criteria.get('blocked_seller_domains', [])}
        seller = ('unknown' if not valid_host else 'unsatisfied'
                  if host in blocked or (allowed and host not in allowed) else 'satisfied')
        add('seller_policy', seller, 'Exact source hostname policy: ' + (host or 'missing'))
        observed = datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00'))
        age = now - observed
        fresh = timedelta(0) <= age <= timedelta(minutes=min(
            goal['monitoring']['max_evidence_age_minutes'], 30))
        add('freshness', 'satisfied' if fresh else 'unknown',
            'Observation timestamp is within freshness window' if fresh else
            'Observation is stale or future-dated; request time does not refresh cached evidence')
        checked = observation['evidence']['reliability'] == 'checked'
        add('reliability', 'satisfied' if checked else 'unknown',
            'Evidence reliability: ' + observation['evidence']['reliability'])
        shipping, tax = observation['shipping_eur_cents'], observation['tax_eur_cents']
        for code, amount in (('tax_known', tax), ('shipping_known', shipping)):
            add(code, 'satisfied' if amount is not None and checked else 'unknown',
                'Checked charge: ' + str(amount) + ' EUR cents' if amount is not None and checked else
                'Charge missing or evidence not checked')
        pieces = observation['selling_unit'] == 'pcs'
        add('units_compatible', 'satisfied' if pieces else 'unknown',
            'Price is per piece' if pieces else 'No approved conversion to requested pieces')
        total = None
        if pieces and shipping is not None and tax is not None:
            subtotal = criteria['quantity'] * observation['unit_price_eur_cents']
            total = subtotal + shipping + tax
            add('total_within_budget', 'satisfied' if total <= criteria['max_total_eur_cents'] else 'unsatisfied',
                f'{criteria["quantity"]} x {observation["unit_price_eur_cents"]} + {shipping} shipping + '
                f'{tax} additional tax = {total} EUR cents; cap {criteria["max_total_eur_cents"]}')
        else:
            lower_bound = (criteria['quantity'] * observation['unit_price_eur_cents'] +
                           sum(amount for amount in (shipping, tax) if amount is not None)) if pieces else None
            if lower_bound is not None and lower_bound > criteria['max_total_eur_cents']:
                add('total_within_budget', 'unsatisfied',
                    f'Known payable components already total {lower_bound} EUR cents, above cap '
                    f'{criteria["max_total_eur_cents"]}; missing charges remain unknown')
            else:
                add('total_within_budget', 'unknown', 'Complete payable total cannot be calculated')
        return conditions, total
