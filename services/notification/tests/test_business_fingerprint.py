"""Canonical P0 key vectors to hand to GE/D1 without private module imports."""

import hashlib
from uuid import uuid4

import pytest

from test_notifications import setup
from notification.fingerprint import business_fingerprint, offer_fingerprint


def test_canonical_product_vector_is_exactly_the_adr_field_order(setup):
    s = setup
    o = s.observation
    canonical = '|'.join([
        s.goal['goal_id'], '1', 'example.com', 'rtx 5070', 'EUR',
        str(o['unit_price_eur_cents'] + o['shipping_eur_cents'] + o['tax_eur_cents']),
        str(o['shipping_eur_cents']), str(o['tax_eur_cents']), 'unknown', 'FI',
    ])
    assert business_fingerprint(s.goal, [o]) == hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def test_listing_paths_captures_and_display_name_do_not_change_business_key(setup):
    s = setup
    changed = dict(s.observation, observation_id=str(uuid4()), source_id=str(uuid4()),
                   observed_at='2026-11-14T09:59:00Z', product_name='Different display wording',
                   source_url='https://EXAMPLE.com/other-product-path?tracking=another')
    assert business_fingerprint(s.goal, [changed]) == business_fingerprint(s.goal, [s.observation])


@pytest.mark.parametrize('field', ['unit_price_eur_cents', 'shipping_eur_cents', 'tax_eur_cents'])
def test_changed_payable_component_creates_distinct_key(setup, field):
    s = setup
    changed = dict(s.observation)
    changed[field] += 1
    assert business_fingerprint(s.goal, [changed]) != business_fingerprint(s.goal, [s.observation])


def test_goal_version_and_seller_are_in_business_key(setup):
    s = setup
    key = business_fingerprint(s.goal, [s.observation])
    assert business_fingerprint(dict(s.goal, version=2), [s.observation]) != key
    assert business_fingerprint(s.goal, [dict(s.observation, source_url='https://other.example.com/item')]) != key


def test_goal_uuid_case_does_not_change_canonical_identity(setup):
    s = setup
    assert business_fingerprint(dict(s.goal, goal_id=s.goal['goal_id'].upper()), [s.observation]) == \
        business_fingerprint(s.goal, [s.observation])


def test_relisted_equivalent_capture_cannot_notify_twice(setup):
    s = setup
    assert s.consumer.handle(s.event) == 'created'
    s.event['event_id'] = str(uuid4())
    s.observation['source_url'] = 'https://example.com/another-path?same-product=1'
    assert s.consumer.handle(s.event) == 'deduplicated'
    assert len(s.store.notifications) == 1


def test_exact_phase1_key_remains_recognized_during_upgrade(setup):
    s = setup
    assert s.consumer.handle(s.event) == 'created'
    note = next(iter(s.store.notifications.values()))
    legacy = offer_fingerprint(s.goal, [s.observation])
    note['offer_fingerprint'] = legacy
    s.store.notifications = {(note['user_id'], note['goal_id'], note['goal_version'], legacy, 'in_app'): note}
    s.event['event_id'] = str(uuid4())
    assert s.consumer.handle(s.event) == 'deduplicated'


@pytest.mark.parametrize('shipping', [None, 1.0, True])
def test_unknown_or_noninteger_components_cannot_get_a_business_key(setup, shipping):
    s = setup
    with pytest.raises(ValueError):
        business_fingerprint(s.goal, [dict(s.observation, shipping_eur_cents=shipping)])
