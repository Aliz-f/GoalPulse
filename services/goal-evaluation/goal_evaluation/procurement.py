"""Bounded, deterministic procurement baskets with explicit normalization gates."""

import itertools
import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Sequence
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from .domain import Contracts, Document
from .product import aggregate, normalized

CODES = ('full_basket_coverage', 'units_compatible', 'quality', 'availability',
         'delivery', 'seller_limit', 'freshness', 'reliability',
         'shipping_known_by_seller', 'tax_known', 'total_within_budget')


def line_cost(quantity: object, price: int) -> int:
    """Round each normalized-quantity line once, to cents, with ROUND_HALF_UP."""
    number = Decimal(str(quantity))
    if not number.is_finite() or number <= 0:
        raise ValueError('Quantity must be finite and positive')
    with localcontext() as context:
        context.prec = max(28, len(number.as_tuple().digits) + abs(number.as_tuple().exponent) +
                           len(str(price)) + 4)
        return int((number * price).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def hostname(observation: Document) -> str | None:
    try:
        url = urlsplit(observation['source_url'])
        if url.username is not None or url.password is not None:
            return None
        return url.hostname.lower().rstrip('.') if url.hostname else None
    except ValueError:
        return None


class RecurringProcurementEvaluator:
    """One complete option per item, exhaustive search within an explicit bound.

    A true normalization flag is appropriate only for confirmed synthetic
    fixtures or an approved D3 normalization handoff. It confirms price per unit,
    additional tax per requested line, and shipping per merchant order for this
    basket. It does NOT establish quality or any missing observation facts.
    Defaults never assert that the absent D3 dependency has been delivered.
    """

    version = 'procurement-v1.1'

    def __init__(self, contracts: Contracts, *, normalized_cost_policy_confirmed: bool = False,
                 max_combinations: int = 10000, max_items: int = 20, max_observations: int = 200):
        if type(normalized_cost_policy_confirmed) is not bool:
            raise ValueError('Normalization confirmation must be explicit boolean')
        for bound in (max_combinations, max_items, max_observations):
            if type(bound) is not int or not 1 <= bound <= 100000:
                raise ValueError('Invalid optimizer bound')
        self.contracts = contracts
        self.confirmed = normalized_cost_policy_confirmed
        self.max_combinations, self.max_items, self.max_observations = max_combinations, max_items, max_observations

    def evaluate(self, goal: Document, observations: Sequence[Document], now_utc: datetime) -> Document:
        self.contracts.validate('goal-spec', goal)
        if goal['goal_type'] != 'recurring_procurement':
            raise ValueError('RecurringProcurementEvaluator requires recurring_procurement')
        if now_utc.utcoffset() != timedelta(0):
            raise ValueError('Evaluation clock must be timezone-aware UTC')
        criteria = goal['criteria']
        for key in ('current_total_eur_cents', 'max_total_eur_cents'):
            if type(criteria[key]) is not int:
                raise ValueError('Money must use integer EUR cents')
        by_id = {}
        for observation in observations:
            self.contracts.validate('observation', observation)
            for key in ('unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents'):
                if observation[key] is not None and type(observation[key]) is not int:
                    raise ValueError('Money must use integer EUR cents')
            oid = observation['observation_id']
            if oid in by_id and by_id[oid] != observation:
                raise ValueError('Conflicting immutable observation')
            by_id[oid] = observation
        items = criteria['items']
        names = [normalized(item['name']) for item in items]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError('Item names must be nonempty and distinct after normalization')
        for item in items:
            line_cost(item['quantity'], 0)  # reject non-finite quantities before search
        bounded = len(items) > self.max_items or len(observations) > self.max_observations
        options = [] if bounded else [[o for o in sorted(by_id.values(), key=lambda o: o['observation_id'])
                    if normalized(o['product_name']) == normalized(item['name'])] or [None]
                   for item in items]
        bounded = bounded or math.prod(len(row) for row in options) > self.max_combinations
        if bounded:
            conditions = [dict(code=code, status='unknown', reason='Optimizer input/search bound exceeded',
                               observation_ids=[]) for code in CODES]
            selected, total = [], None
        else:
            best = None
            for basket in itertools.product(*options):
                checks, cost = self._basket(goal, basket, now_utc)
                rank = ({'satisfied': 0, 'unknown': 1, 'unsatisfied': 2}[aggregate(checks)],
                        cost is None, cost or 0,
                        tuple(o['observation_id'] if o else '' for o in basket))
                if best is None or rank < best[0]:
                    best = rank, checks, cost, basket
            _, conditions, total, basket = best
            selected = sorted({o['observation_id'] for o in basket if o is not None})
        status = aggregate(conditions)
        result = dict(goal_id=goal['goal_id'], goal_version=goal['version'],
                      evaluated_at=now_utc.isoformat().replace('+00:00', 'Z'), status=status,
                      conditions=conditions, candidate_observation_ids=selected,
                      is_opportunity_verified=status == 'satisfied' and bool(selected))
        # No progress percentage until a verified complete comparable basket exists.
        baseline, cap = criteria['current_total_eur_cents'], criteria['max_total_eur_cents']
        if result['is_opportunity_verified'] and total is not None and baseline > cap:
            with localcontext() as context:
                context.prec = max(28, len(str(baseline)) + 8)
                progress = min(Decimal(100), max(Decimal(0),
                    Decimal(baseline - total) * 100 / Decimal(baseline - cap)))
            result['progress'] = dict(metric='savings_progress', value=float(progress.quantize(Decimal('0.01'))), unit='percent')
        identity = json.dumps(dict(
            evaluator_version=self.version, normalization_confirmed=self.confirmed,
            goal=goal, observations=sorted(by_id.values(), key=lambda o: o['observation_id']), result=result),
            sort_keys=True, separators=(',', ':'), allow_nan=False)
        result['evaluation_id'] = str(uuid5(NAMESPACE_URL, hashlib.sha256(identity.encode()).hexdigest()))
        self.contracts.validate('goal-evaluation', result)
        return result

    def _basket(self, goal: Document, basket: Sequence[Document | None], now: datetime):
        criteria, conditions = goal['criteria'], []
        observations = [o for o in basket if o is not None]
        ids = sorted({o['observation_id'] for o in observations})
        complete = len(observations) == len(criteria['items'])

        def add(code: str, status: str, reason: str) -> None:
            conditions.append(dict(code=code, status=status, reason=reason, observation_ids=ids))

        def combined(values: Sequence[str]) -> str:
            return ('unsatisfied' if 'unsatisfied' in values else 'unknown'
                    if not values or 'unknown' in values else 'satisfied')

        add('full_basket_coverage', 'satisfied' if complete else 'unknown',
            'Every exact item name is covered' if complete else 'At least one item has no exact-name observation')
        compatible = complete and all(o['selling_unit'] == item['unit'] and
            (item['unit'] != 'pcs' or Decimal(str(item['quantity'])) ==
             Decimal(str(item['quantity'])).to_integral_value())
            for item, o in zip(criteria['items'], basket) if o is not None)
        add('units_compatible', 'satisfied' if compatible and self.confirmed else 'unknown',
            'Confirmed normalized units match' if compatible and self.confirmed else
            'Unit/package normalization or integral piece quantity cannot be established')
        add('quality', 'satisfied' if complete and all(not i['requirements'] for i in criteria['items']) else 'unknown',
            'No additional quality requirements' if all(not i['requirements'] for i in criteria['items']) else
            'Observation.v1 has no structured quality requirements evidence')
        availability = []
        for item, observation in zip(criteria['items'], basket):
            if observation is None or observation['availability'] == 'unknown':
                availability.append('unknown')
            elif observation['availability'] == 'out_of_stock':
                availability.append('unsatisfied')
            elif Decimal(str(item['quantity'])) == Decimal('1'):
                availability.append('satisfied')
            else:
                # Observation.v1 has only a tri-state listing flag. It does not
                # prove that the requested kg/L/piece quantity is orderable.
                availability.append('unknown')
        availability_status = combined(availability)
        add('availability', availability_status,
            'Every requested quantity is exactly one in-stock normalized unit'
            if availability_status == 'satisfied' else
            'A listing is unavailable, unknown, or lacks requested-quantity stock evidence')
        add('delivery', combined([{'yes': 'satisfied', 'no': 'unsatisfied', 'unknown': 'unknown'}[o['delivery_to_fi']]
            for o in observations]) if criteria['delivery_country'] == 'FI' else 'unknown',
            'Only FI delivery is representable in v1')
        sellers = {hostname(o) for o in observations}
        limit = criteria.get('max_distinct_sellers')
        seller_status = ('unknown' if None in sellers or not observations else
                         'unsatisfied' if limit is not None and len(sellers) > limit else 'satisfied')
        add('seller_limit', seller_status, f'{len(sellers)} distinct source hostnames; limit {limit}')
        freshness = [timedelta(0) <= now - datetime.fromisoformat(o['observed_at'].replace('Z', '+00:00')) <=
                     timedelta(minutes=goal['monitoring']['max_evidence_age_minutes']) for o in observations]
        add('freshness', 'satisfied' if observations and all(freshness) else 'unknown',
            'Original capture timestamps checked; stale/future data cannot verify')
        checked = bool(observations) and all(o['evidence']['reliability'] == 'checked' for o in observations)
        add('reliability', 'satisfied' if checked else 'unknown', 'Every selected line must have checked evidence')
        shipping_total = 0
        shipping_known = complete and self.confirmed and checked and None not in sellers
        for seller in sellers:
            charges = {o['shipping_eur_cents'] for o in observations if hostname(o) == seller}
            if None in charges or len(charges) != 1:
                shipping_known = False
            else:
                shipping_total += next(iter(charges))
        add('shipping_known_by_seller', 'satisfied' if shipping_known else 'unknown',
            'Consistent shipping applied once per seller' if shipping_known else
            'Merchant shipping missing, inconsistent or normalization unconfirmed')
        tax_known = complete and checked and self.confirmed and all(o['tax_eur_cents'] is not None for o in observations)
        add('tax_known', 'satisfied' if tax_known else 'unknown',
            'Confirmed additional tax applied once per selected line' if tax_known else 'Additional line tax is not established')
        total = None
        if complete and compatible and shipping_known and tax_known:
            lines = [line_cost(item['quantity'], o['unit_price_eur_cents'])
                     for item, o in zip(criteria['items'], basket)]
            total = sum(lines) + shipping_total + sum(o['tax_eur_cents'] for o in observations)
            add('total_within_budget', 'satisfied' if total <= criteria['max_total_eur_cents'] else 'unsatisfied',
                f'ROUND_HALF_UP line cents {lines} + merchant shipping {shipping_total} + additional line tax '
                f'{sum(o["tax_eur_cents"] for o in observations)} = {total} EUR cents; cap {criteria["max_total_eur_cents"]}')
        else:
            add('total_within_budget', 'unknown', 'Complete normalized payable basket total cannot be established')
        return conditions, total
