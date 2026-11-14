"""Whitelisted prepare-only packages using a service-local proposed seller grouping."""
import copy
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator, FormatChecker
from .eligibility import Document, HandoffEligibility, Runtime, safe_source


def normalized(value: str) -> str:
    return ' '.join(value.casefold().split())


def amount(quantity: object, price: int) -> int:
    q = Decimal(str(quantity))
    if not q.is_finite() or q <= 0 or type(price) is not int or price < 0:
        raise ValueError('Invalid quantity/price')
    with localcontext() as context:
        context.prec = max(28, len(q.as_tuple().digits) + abs(q.as_tuple().exponent) + len(str(price)) + 4)
        return int((q * price).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class PackageContract:
    def __init__(self, path: Path | None = None):
        path = path or Path(__file__).resolve().parents[1] / 'schemas/action-package.v1.schema.json'
        self.validator = Draft202012Validator(json.loads(path.read_text()), format_checker=FormatChecker())

    def validate(self, package: Document) -> None:
        self.validator.validate(package)
        created = datetime.fromisoformat(package['created_at'].replace('Z', '+00:00'))
        expires = datetime.fromisoformat(package['expires_at'].replace('Z', '+00:00'))
        if expires <= created:
            raise ValueError('Package has no usable lifetime')
        total, sellers, ids = 0, set(), set()
        for order in package['seller_orders']:
            seller = order['seller_domain']
            if seller in sellers:
                raise ValueError('Repeated seller order')
            sellers.add(seller)
            if type(order['shipping_eur_cents']) is not int:
                raise ValueError('Money must use integer EUR cents')
            total += order['shipping_eur_cents']
            for item in order['items']:
                if not safe_source(item['source_url']) or urlsplit(item['source_url']).hostname.lower().rstrip('.') != seller:
                    raise ValueError('Package seller/source mismatch')
                if item['observation_id'] in ids:
                    raise ValueError('Repeated package evidence')
                ids.add(item['observation_id'])
                observed = datetime.fromisoformat(item['observed_at'].replace('Z', '+00:00'))
                if observed > created or any(type(item[k]) is not int for k in (
                    'unit_price_eur_cents', 'total_item_eur_cents', 'additional_tax_eur_cents')):
                    raise ValueError('Invalid capture time or integer money')
                if item['unit'] == 'pcs' and Decimal(item['quantity']) != Decimal(item['quantity']).to_integral_value():
                    raise ValueError('Fractional pieces are unsupported')
                if amount(item['quantity'], item['unit_price_eur_cents']) != item['total_item_eur_cents']:
                    raise ValueError('Incorrect line total')
                total += item['total_item_eur_cents'] + item['additional_tax_eur_cents']
        if type(package['total_eur_cents']) is not int or total != package['total_eur_cents']:
            raise ValueError('Incorrect package total')


class PackageBuilder:
    def __init__(self, eligibility: HandoffEligibility, contract: PackageContract,
                 *, procurement_normalization_confirmed: bool = False):
        if type(procurement_normalization_confirmed) is not bool:
            raise ValueError('Normalization confirmation must be boolean')
        self.eligibility, self.contract = eligibility, contract
        self.procurement_confirmed = procurement_normalization_confirmed

    def build(self, event: Document, goal: Document, evaluation: Document,
              observations: Sequence[Document], runtime: Runtime, now: datetime) -> Document | None:
        check = self.eligibility.check(event, goal, evaluation, observations, runtime, now)
        if not check.allowed:
            return None
        by_id = {o['observation_id']: o for o in observations}
        selected = [by_id[oid] for oid in sorted(evaluation['candidate_observation_ids'])]
        criteria = goal['criteria']
        if type(criteria['max_total_eur_cents']) is not int:
            raise ValueError('Budget must use integer EUR cents')
        lines = []
        if goal['goal_type'] == 'product_purchase':
            if len(selected) != 1 or selected[0]['selling_unit'] != 'pcs' or type(criteria['quantity']) is not int:
                return None
            o, product = selected[0], criteria['product']
            names = {normalized(o['model'])} if o.get('model') else set()
            if names and o.get('brand'):
                names.add(normalized(o['brand'] + ' ' + o['model']))
            if normalized(product['query']) not in names or any(
                not o.get(key) or normalized(o[key]) != normalized(value)
                for key, value in product.get('required_attributes', {}).items()
            ):
                return None
            seller = urlsplit(o['source_url']).hostname.lower().rstrip('.')
            blocked = {d.lower().rstrip('.') for d in criteria.get('blocked_seller_domains', [])}
            allowed = {d.lower().rstrip('.') for d in criteria.get('allowed_seller_domains', [])}
            if seller in blocked or (allowed and seller not in allowed):
                return None
            lines = [(o, criteria['quantity'])]
        else:
            if not self.procurement_confirmed or len(selected) != len(criteria['items']):
                return None
            used = set()
            for item in criteria['items']:
                matches = [o for o in selected if normalized(o['product_name']) == normalized(item['name']) and
                           o['selling_unit'] == item['unit']]
                if len(matches) != 1 or matches[0]['observation_id'] in used:
                    return None
                quantity = Decimal(str(item['quantity']))
                if not quantity.is_finite() or quantity <= 0 or (item['unit'] == 'pcs' and quantity != quantity.to_integral_value()):
                    return None
                used.add(matches[0]['observation_id'])
                lines.append((matches[0], quantity))
        orders = {}
        for o, quantity in lines:
            seller = urlsplit(o['source_url']).hostname.lower().rstrip('.')
            order = orders.setdefault(seller, dict(seller_domain=seller, shipping_eur_cents=o['shipping_eur_cents'], items=[]))
            if order['shipping_eur_cents'] != o['shipping_eur_cents']:
                return None
            quantity_text = format(Decimal(str(quantity)), 'f')
            if '.' in quantity_text:
                quantity_text = quantity_text.rstrip('0').rstrip('.')
            order['items'].append(dict(source_url=o['source_url'], observation_id=o['observation_id'],
                observed_at=o['observed_at'], item_name=o['product_name'], quantity=quantity_text,
                unit=o['selling_unit'], unit_price_eur_cents=o['unit_price_eur_cents'],
                total_item_eur_cents=amount(quantity, o['unit_price_eur_cents']), additional_tax_eur_cents=o['tax_eur_cents']))
        if goal['goal_type'] == 'recurring_procurement' and len(orders) > criteria.get('max_distinct_sellers', len(orders)):
            return None
        total = sum(order['shipping_eur_cents'] + sum(i['total_item_eur_cents'] + i['additional_tax_eur_cents']
                    for i in order['items']) for order in orders.values())
        if total > criteria['max_total_eur_cents']:
            return None
        package = dict(package_id=str(uuid5(NAMESPACE_URL,
            f'prepare_only:{goal["goal_id"]}:{goal["version"]}:{evaluation["evaluation_id"]}')),
            schema_version=1, goal_id=goal['goal_id'], goal_version=goal['version'], evaluation_id=evaluation['evaluation_id'],
            created_at=now.isoformat().replace('+00:00', 'Z'), expires_at=check.expires_at.isoformat().replace('+00:00', 'Z'),
            action_type='prepare_purchase', approval_required=True, auto_execute=False,
            seller_orders=[orders[seller] for seller in sorted(orders)], total_eur_cents=total,
            condition_codes=sorted(c['code'] for c in evaluation['conditions']), next_action='request_user_approval',
            requires_checkout_revalidation=True)
        self.contract.validate(package)
        return copy.deepcopy(package)
