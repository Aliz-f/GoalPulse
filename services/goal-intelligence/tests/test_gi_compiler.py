"""D2-GI-03: classification, Decimal EUR, missing fields, one question at a time,
explicit approval of defaults, and GoalSpec-valid candidates."""

from __future__ import annotations

import pytest
from conftest import USER_A, assemble_goal_spec, converse

from goal_intelligence.domain.compiler import WorkingState, compile_turn, summary_text
from goal_intelligence.domain.extraction import ExtractionPatch, ItemPatch
from goal_intelligence.domain.money import MoneyError, cents_after_savings, parse_eur_to_cents

PRODUCT_FLOW = [
    "I want to buy an RTX 5070 for under €600",
    "yes",
    "new only",
    "equivalent models are fine",
    "yes",
]
CAFE_FLOW = [
    "Our café needs 20 L lactose-free milk and 10 kg coffee beans every month. "
    "We currently spend €250 and want to save 15%",
    "milk: lactose free; beans: whole beans",
    "Yes, delivered to Helsinki",
    "sounds good",
]


@pytest.mark.parametrize(
    ("raw", "cents"),
    [("600", 60000), ("212.50", 21250), ("212,50", 21250), ("1 299,90", 129990), ("1,299", 129900),
     ("1.299,90", 129990), ("0.99", 99), ("€45", 4500)],
)
def test_parse_eur(raw, cents):
    assert parse_eur_to_cents(raw) == cents


@pytest.mark.parametrize("raw", ["", "-5", "abc", "1.999", "12.345", "99999999", "1e3"])
def test_parse_eur_rejects(raw):
    with pytest.raises(MoneyError):
        parse_eur_to_cents(raw)


def test_product_summary_includes_every_required_attribute():
    state = WorkingState(
        goal_type="product_purchase",
        product_query="Example laptop",
        match_policy="exact_model",
        condition="new",
        quantity=1,
        required_attributes={"memory": "16 GB", "colour": "black"},
        max_total_eur_cents=100000,
        delivery_country="FI",
        include_shipping_and_tax=True,
        frequency_minutes=1440,
        max_evidence_age_minutes=60,
        notify_user=True,
    )
    summary = summary_text(state)
    assert "colour: black" in summary
    assert "memory: 16 GB" in summary


def test_parse_eur_refuses_floats():
    with pytest.raises(MoneyError):
        parse_eur_to_cents(599.99)  # type: ignore[arg-type]


def test_savings_round_down_to_cent():
    assert cents_after_savings(25000, "15") == 21250
    assert cents_after_savings(999, "33.3") == 666  # 666.333 -> 666, never above target


def test_product_incomplete_asks_shipping_then_condition(service):
    draft = service.create_session(USER_A, PRODUCT_FLOW[0])
    assert draft.state == "collecting" and not draft.ready_for_confirmation
    assert {"include_shipping_and_tax", "delivery_country", "product_condition", "match_policy"} <= set(draft.missing_fields)
    assert "shipping" in draft.next_question.lower() and "Finland" in draft.next_question
    assert draft.candidate_goal["criteria"]["max_total_eur_cents"] == 60000
    draft = service.post_message(USER_A, draft.session_id, "yes", draft.revision)
    assert draft.missing_fields[0] == "product_condition" and "new" in draft.next_question.lower()


def test_product_flow_becomes_ready_and_schema_valid(service, goal_spec_validator, goal_draft_validator):
    draft = converse(service, USER_A, PRODUCT_FLOW)
    assert draft.ready_for_confirmation and draft.state == "ready_for_confirmation"
    assert draft.missing_fields == [] and draft.next_question is None and draft.revision == 5
    goal_draft_validator.validate(draft.model_dump())
    spec = assemble_goal_spec(draft.candidate_goal)
    goal_spec_validator.validate(spec)
    c = spec["criteria"]
    assert c["product"] == {"query": "RTX 5070", "match_policy": "equivalent_allowed", "condition": "new"}
    assert c["max_total_eur_cents"] == 60000 and isinstance(c["max_total_eur_cents"], int)
    assert c["delivery_country"] == "FI" and c["include_shipping_and_tax"] is True
    assert spec["actions"] == {"notify_user": True, "agent_handoff": "prepare_only", "auto_purchase": False}


def test_cafe_incomplete_asks_quantity_quality_budget(service):
    draft = service.create_session(USER_A, "Our café needs milk and coffee beans every month")
    assert draft.missing_fields[0] == "items"
    draft = service.post_message(USER_A, draft.session_id, "20 milk and 10 kg coffee beans", draft.revision)
    assert draft.missing_fields[0] == "items.quantity_unit"
    assert "Milk" in draft.next_question and "Coffee" not in draft.next_question
    draft = service.post_message(USER_A, draft.session_id, "milk 20 litres", draft.revision)
    assert draft.missing_fields[0] == "items.requirements"
    draft = service.post_message(USER_A, draft.session_id, "none", draft.revision)
    assert draft.missing_fields[0] == "current_total_eur_cents"
    draft = service.post_message(USER_A, draft.session_id, "about 250", draft.revision)
    assert draft.missing_fields[0] == "max_total_eur_cents"
    assert not draft.ready_for_confirmation


def test_cafe_flow_ready_with_requirements(service, goal_spec_validator):
    draft = converse(service, USER_A, CAFE_FLOW)
    assert draft.ready_for_confirmation
    spec = assemble_goal_spec(draft.candidate_goal)
    goal_spec_validator.validate(spec)
    c = spec["criteria"]
    assert c["current_total_eur_cents"] == 25000 and c["max_total_eur_cents"] == 21250
    items = {i["name"]: i for i in c["items"]}
    assert items["Lactose-free milk"] == {"name": "Lactose-free milk", "quantity": 20, "unit": "L", "requirements": ["lactose free"]}
    assert items["Coffee beans"]["requirements"] == ["whole beans"]
    assert c["max_distinct_sellers"] == 3 and spec["monitoring"]["max_evidence_age_minutes"] == 240


def test_target_above_current_spend_is_refused(service):
    draft = service.create_session(
        USER_A, "Our café buys 20 L milk monthly. We currently spend €250 and want it under €300"
    )
    assert "max_total_eur_cents" not in draft.candidate_goal["criteria"]
    assert "wouldn't be a saving" in draft.next_question


@pytest.mark.parametrize(
    "text", ["Book me a flight to Rome under 200 EUR", "Find me a hotel in Lapland", "Help me find a job in Espoo"]
)
def test_unsupported_goals_never_executable(service, text):
    draft = service.create_session(USER_A, text)
    assert draft.missing_fields == ["supported_goal_type"] and not draft.ready_for_confirmation
    assert "can't be tracked yet" in draft.next_question
    for answer in ("yes", "yes", "sounds good"):
        draft = service.post_message(USER_A, draft.session_id, answer, draft.revision)
        assert not draft.ready_for_confirmation
    assert "criteria" not in draft.candidate_goal


def test_unsupported_then_redirected_to_product(service):
    draft = service.create_session(USER_A, "Book a flight to Rome")
    draft = service.post_message(USER_A, draft.session_id, "ok then I want to buy a Kindle Paperwhite", draft.revision)
    assert draft.candidate_goal["goal_type"] == "product_purchase"
    assert draft.candidate_goal["criteria"]["product"]["query"] == "Kindle Paperwhite"


def test_defaults_are_visible_and_need_explicit_approval(service):
    draft = converse(service, USER_A, PRODUCT_FLOW[:-1])
    assert draft.missing_fields == ["summary_approval"]
    q = draft.next_question
    for fragment in ("RTX 5070", "EUR 600.00", "every day", "1 hour", "without ever placing an order", "Finland"):
        assert fragment in q
    assert draft.candidate_goal["monitoring"] == {"frequency_minutes": 1440, "max_evidence_age_minutes": 60}


def test_change_after_summary_requires_reapproval(service):
    draft = converse(service, USER_A, PRODUCT_FLOW[:-1])
    draft = service.post_message(USER_A, draft.session_id, "check every 6 hours", draft.revision)
    assert draft.missing_fields == ["summary_approval"] and "every 6 hours" in draft.next_question
    draft = service.post_message(USER_A, draft.session_id, "yes", draft.revision)
    assert draft.ready_for_confirmation
    assert draft.candidate_goal["monitoring"]["frequency_minutes"] == 360


def test_yes_without_summary_question_does_not_approve():
    state = WorkingState(goal_type="product_purchase")
    result = compile_turn(state, ExtractionPatch(approve_summary=True, product_query="RTX 5070"))
    assert not result.ready and not result.state.summary_approved


def test_schema_failure_fails_closed(monkeypatch):
    from goal_intelligence.domain import compiler

    state = WorkingState(
        goal_type="product_purchase", product_query="RTX 5070", match_policy="exact_model", condition="new",
        quantity=1, max_total_eur_cents=60000, delivery_country="FI", include_shipping_and_tax=True,
        asked_fields=["summary_approval"],
    )
    monkeypatch.setattr(compiler, "goal_spec_errors", lambda c: ["forced failure"])
    result = compile_turn(state, ExtractionPatch(approve_summary=True))
    assert not result.ready and result.missing_fields == ["summary_approval"]


def test_ambiguous_units_are_asked_not_guessed():
    state = WorkingState(goal_type="recurring_procurement")
    result = compile_turn(state, ExtractionPatch(items=[ItemPatch(name="Coffee", quantity="3")]))
    assert result.missing_fields[0] == "items.quantity_unit"
    assert "unit" not in result.candidate_goal["criteria"]["items"][0]


def test_fixture_does_not_map_packs_to_a_unit(service):
    draft = service.create_session(USER_A, "Our café needs 3 bags of coffee beans every month")
    assert draft.missing_fields[0] == "items.quantity_unit"


def test_title_not_hardcoded(service):
    draft = converse(service, USER_A, [
        "I need 2 Logitech MX Master 3S mice for under 180 euros", "yes", "new", "exactly this model", "yes",
    ])
    assert draft.ready_for_confirmation
    assert draft.candidate_goal["criteria"]["quantity"] == 2
    assert draft.candidate_goal["title"] == "Buy 2 × Logitech MX Master 3S mice at or under EUR 180.00 delivered"
