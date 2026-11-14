"""Goal compiler: applies untrusted extraction patches to a working draft,
computes missing fields deterministically, picks the next clarifying question
and decides readiness against the frozen GoalSpec.v1 schema.

Rules enforced here (not by the model):
- only ``product_purchase`` and ``recurring_procurement`` can become ready;
- money is integer EUR cents parsed with Decimal; non-EUR amounts are refused;
- delivery is limited to FI (ICP-04) and budgets must include shipping and tax;
- ``agent_handoff`` is always ``prepare_only`` and ``auto_purchase`` always false;
- proposed monitoring defaults are shown to the user and need an explicit yes;
  any later change withdraws that approval so the user re-reviews the summary.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field

from .extraction import Classification, ExtractionPatch, ItemPatch, Unit
from .money import MoneyError, cents_after_savings, format_cents, parse_eur_to_cents

SUPPORTED = ("product_purchase", "recurring_procurement")
SUPPORTED_DELIVERY_COUNTRIES = ("FI",)  # ICP-04: Observation.v1 only evidences delivery_to_fi

# Proposals shown in the summary; they become part of the goal only after a yes.
DEFAULTS = {
    "product_purchase": {"frequency_minutes": 1440, "max_evidence_age_minutes": 60},
    "recurring_procurement": {"frequency_minutes": 1440, "max_evidence_age_minutes": 240},
}
DEFAULT_MAX_DISTINCT_SELLERS = 3

MAX_QUANTITY = 10_000


class ItemState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    quantity: str | None = None  # Decimal string, never float
    unit: Unit | None = None
    requirements: list[str] | None = None  # None = not answered yet


class WorkingState(BaseModel):
    """Everything GI knows about the draft. Persisted per immutable revision."""

    model_config = ConfigDict(extra="forbid")

    goal_type: Classification | None = None
    unsupported_reason: str | None = None

    product_query: str | None = None
    match_policy: str | None = None
    condition: str | None = None
    quantity: int | None = None
    required_attributes: dict[str, str] | None = None

    items: list[ItemState] = Field(default_factory=list)
    current_total_eur_cents: int | None = None
    max_distinct_sellers: int | None = None

    max_total_eur_cents: int | None = None
    delivery_country: str | None = None
    include_shipping_and_tax: bool | None = None

    frequency_minutes: int | None = None
    max_evidence_age_minutes: int | None = None
    notify_user: bool | None = None
    summary_approved: bool = False

    asked_fields: list[str] = Field(default_factory=list)
    last_question: str | None = None


@dataclass
class CompileResult:
    state: WorkingState
    candidate_goal: dict[str, Any]
    missing_fields: list[str]
    next_question: str | None
    ready: bool
    notices: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- schema


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "contracts" / "schemas").is_dir():
            return parent
    raise RuntimeError("contracts/schemas not found; set GI_CONTRACTS_DIR")


@lru_cache(maxsize=4)
def _validator(contracts_dir: str | None = None) -> Draft202012Validator:
    import os

    base = Path(contracts_dir or os.environ.get("GI_CONTRACTS_DIR") or _repo_root() / "contracts")
    schema = json.loads((base / "schemas" / "goal-spec.v1.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def goal_spec_errors(candidate: dict[str, Any]) -> list[str]:
    """Validate a candidate as GM would assemble it (server-managed fields filled in)."""
    probe = copy.deepcopy(candidate)
    probe.setdefault("goal_id", str(uuid.UUID(int=0)))
    probe.setdefault("owner_user_id", str(uuid.UUID(int=0)))
    probe.setdefault("version", 1)
    probe.setdefault("approved_at", "2000-01-01T00:00:00Z")
    return sorted(e.message for e in _validator().iter_errors(probe))


# --------------------------------------------------------------------------- apply


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _decimal_qty(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw.replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None
    if value <= 0 or value > MAX_QUANTITY:
        return None
    return format(value.normalize(), "f")


def _merge_items(state: WorkingState, patch_items: list[ItemPatch], notices: list[str]) -> bool:
    changed = False
    by_name = {_norm(i.name): i for i in state.items}
    for p in patch_items:
        key = _norm(p.name)
        target = by_name.get(key)
        if target is None:
            target = ItemState(name=p.name.strip()[:120])
            state.items.append(target)
            by_name[key] = target
            changed = True
        if p.quantity is not None:
            qty = _decimal_qty(p.quantity)
            if qty is None:
                notices.append(f"I couldn't use the quantity '{p.quantity}' for {target.name}.")
            elif qty != target.quantity:
                target.quantity, changed = qty, True
        if p.unit is not None and p.unit != target.unit:
            target.unit, changed = p.unit, True
        if p.requirements is not None:
            reqs = [r.strip() for r in p.requirements if r and r.strip()]
            if reqs != target.requirements:
                target.requirements, changed = reqs, True
    return changed


def _apply_item_requirements(state: WorkingState, reqs: dict[str, list[str]]) -> bool:
    changed = False
    for key, values in reqs.items():
        clean = [v.strip() for v in values if v and v.strip()]
        targets = state.items if key == "*" else [
            i for i in state.items if _norm(key) in _norm(i.name) or _norm(i.name) in _norm(key)
        ]
        for item in targets:
            if item.requirements != clean:
                item.requirements, changed = clean, True
    return changed


def apply_patch(prev: WorkingState, patch: ExtractionPatch) -> tuple[WorkingState, list[str]]:
    """Return a new state with every acceptable fact from ``patch`` applied."""
    state = prev.model_copy(deep=True)
    notices: list[str] = []
    changed = False
    asked = set(prev.asked_fields)

    # 1. classification: only settles an undecided draft; switching type needs a new session
    if patch.classification and state.goal_type not in SUPPORTED:
        if patch.classification in SUPPORTED:
            state.goal_type, state.unsupported_reason = patch.classification, None
            changed = True
        elif patch.classification == "unsupported":
            state.goal_type = "unsupported"
            state.unsupported_reason = (patch.unsupported_reason or "That kind of goal")[:200]
        elif state.goal_type is None:
            state.goal_type = "unclear"

    if patch.auto_purchase_requested:
        notices.append(
            "GoalPulse never buys anything automatically: it finds verified offers and prepares "
            "the purchase details for you to complete yourself."
        )
        state.summary_approved = False

    # 2. currency: no FX assumptions
    money_ok = True
    if patch.currency_seen and patch.currency_seen.upper() not in ("EUR", "€"):
        money_ok = False
        if any(v is not None for v in (patch.max_total_eur, patch.current_total_eur)):
            notices.append(
                f"I can only work with amounts in euros and won't convert from {patch.currency_seen.upper()}."
            )

    def money(raw: str | None, label: str) -> int | None:
        if raw is None or not money_ok:
            return None
        try:
            return parse_eur_to_cents(raw)
        except MoneyError:
            notices.append(f"I couldn't read '{raw}' as an amount in euros for the {label}.")
            return None

    current = money(patch.current_total_eur, "current monthly spend")
    if current is not None and current != state.current_total_eur_cents:
        state.current_total_eur_cents, changed = current, True

    max_total = money(patch.max_total_eur, "budget")
    if max_total is None and patch.savings_percent and state.current_total_eur_cents is not None:
        try:
            max_total = cents_after_savings(state.current_total_eur_cents, patch.savings_percent)
        except MoneyError:
            notices.append(f"I couldn't use '{patch.savings_percent}' as a saving percentage.")
    if max_total is not None:
        if (
            state.goal_type == "recurring_procurement"
            and state.current_total_eur_cents is not None
            and max_total > state.current_total_eur_cents
        ):
            notices.append(
                f"The target ({format_cents(max_total)}) is above what you spend now "
                f"({format_cents(state.current_total_eur_cents)}), so it wouldn't be a saving."
            )
        elif max_total != state.max_total_eur_cents:
            state.max_total_eur_cents, changed = max_total, True

    # 3. delivery and cost basis
    if patch.delivery_country:
        if patch.delivery_country in SUPPORTED_DELIVERY_COUNTRIES:
            if state.delivery_country != patch.delivery_country:
                state.delivery_country, changed = patch.delivery_country, True
        else:
            notices.append("For now GoalPulse can only check offers delivered to Finland.")
    if patch.include_shipping_and_tax is not None:
        if patch.include_shipping_and_tax:
            if state.include_shipping_and_tax is not True:
                state.include_shipping_and_tax, changed = True, True
        else:
            notices.append(
                "The budget has to include shipping and taxes, otherwise a cheap-looking offer "
                "could end up over budget."
            )

    # 4. product facts
    if patch.product_query and len(patch.product_query.strip()) >= 3:
        q = patch.product_query.strip()[:200]
        if q != state.product_query:
            state.product_query, changed = q, True
    if patch.match_policy and patch.match_policy != state.match_policy:
        state.match_policy, changed = patch.match_policy, True
    if patch.condition and patch.condition != state.condition:
        state.condition, changed = patch.condition, True
    if patch.quantity is not None:
        if 1 <= patch.quantity <= MAX_QUANTITY:
            if patch.quantity != state.quantity:
                state.quantity, changed = patch.quantity, True
        else:
            notices.append(f"{patch.quantity} isn't a quantity I can track.")
    if patch.required_attributes:
        attrs = {str(k)[:60]: str(v)[:120] for k, v in patch.required_attributes.items()}
        if attrs != state.required_attributes:
            state.required_attributes, changed = attrs, True

    # 5. procurement facts
    if patch.items:
        changed |= _merge_items(state, patch.items, notices)
    if patch.item_requirements:
        changed |= _apply_item_requirements(state, patch.item_requirements)
    if patch.max_distinct_sellers is not None:
        if 1 <= patch.max_distinct_sellers <= 20:
            if patch.max_distinct_sellers != state.max_distinct_sellers:
                state.max_distinct_sellers, changed = patch.max_distinct_sellers, True
        else:
            notices.append("The number of sellers has to be between 1 and 20.")

    # 6. monitoring changes the user asked for
    if patch.frequency_minutes is not None:
        if 10 <= patch.frequency_minutes <= 10080:
            if patch.frequency_minutes != state.frequency_minutes:
                state.frequency_minutes, changed = patch.frequency_minutes, True
        else:
            notices.append("Checks can run between every 10 minutes and once a week.")
    if patch.max_evidence_age_minutes is not None:
        if 5 <= patch.max_evidence_age_minutes <= 10080:
            if patch.max_evidence_age_minutes != state.max_evidence_age_minutes:
                state.max_evidence_age_minutes, changed = patch.max_evidence_age_minutes, True
        else:
            notices.append("Evidence age has to be between 5 minutes and one week.")
    if patch.notify_user is not None and patch.notify_user != state.notify_user:
        state.notify_user, changed = patch.notify_user, True

    # 7. approval: only an explicit yes to the summary question, with nothing changed
    if changed or patch.auto_purchase_requested:
        state.summary_approved = False
    elif patch.approve_summary is True and "summary_approval" in asked:
        state.summary_approved = True

    return state, notices


# --------------------------------------------------------------------------- missing fields


def _fill_proposals(state: WorkingState) -> None:
    """Make proposed defaults visible in the candidate (still need explicit approval)."""
    if state.goal_type not in SUPPORTED:
        return
    d = DEFAULTS[state.goal_type]
    if state.frequency_minutes is None:
        state.frequency_minutes = d["frequency_minutes"]
    if state.max_evidence_age_minutes is None:
        state.max_evidence_age_minutes = d["max_evidence_age_minutes"]
    if state.notify_user is None:
        state.notify_user = True
    if state.goal_type == "recurring_procurement" and state.max_distinct_sellers is None:
        state.max_distinct_sellers = DEFAULT_MAX_DISTINCT_SELLERS


def missing_fields(state: WorkingState) -> list[str]:
    if state.goal_type not in SUPPORTED:
        return ["supported_goal_type"]
    missing: list[str] = []
    if state.goal_type == "product_purchase":
        if not state.product_query:
            missing.append("product_query")
        if state.max_total_eur_cents is None:
            missing.append("max_total_eur_cents")
        if state.include_shipping_and_tax is not True:
            missing.append("include_shipping_and_tax")
        if state.delivery_country is None:
            missing.append("delivery_country")
        if state.condition is None:
            missing.append("product_condition")
        if state.match_policy is None:
            missing.append("match_policy")
        if state.quantity is None:
            missing.append("quantity")
    else:
        if not state.items:
            missing.append("items")
        else:
            if any(i.quantity is None or i.unit is None for i in state.items):
                missing.append("items.quantity_unit")
            if any(i.requirements is None for i in state.items):
                missing.append("items.requirements")
        if state.current_total_eur_cents is None:
            missing.append("current_total_eur_cents")
        if state.max_total_eur_cents is None:
            missing.append("max_total_eur_cents")
        if state.include_shipping_and_tax is not True:
            missing.append("include_shipping_and_tax")
        if state.delivery_country is None:
            missing.append("delivery_country")
    if not missing and not state.summary_approved:
        missing.append("summary_approval")
    return missing


# --------------------------------------------------------------------------- questions


def _duration(minutes: int, *, bare_singular: bool = False) -> str:
    """'every day' / 'every 6 hours' (bare_singular) or '1 hour' / '4 hours'."""
    for size, unit in ((1440, "day"), (60, "hour"), (1, "minute")):
        if minutes % size == 0:
            n = minutes // size
            if n == 1:
                return unit if bare_singular else f"1 {unit}"
            return f"{n} {unit}s"
    return f"{minutes} minutes"  # pragma: no cover


def _qty(item: ItemState) -> str:
    return f"{item.quantity} {item.unit}" if item.quantity and item.unit else "?"


def summary_text(state: WorkingState) -> str:
    """Canonical, user-readable summary with every constraint and proposed default."""
    mon = (
        f"I'll check offers every {_duration(state.frequency_minutes or 0, bare_singular=True)}, only trust evidence up to "
        f"{_duration(state.max_evidence_age_minutes or 0)} old"
    )
    mon += ", notify you in the app" if state.notify_user else ", without notifications"
    mon += ", and prepare the purchase for you without ever placing an order."
    if state.goal_type == "product_purchase":
        cond = "new" if state.condition == "new" else "new or used"
        match = "exactly this model" if state.match_policy == "exact_model" else "equivalent models OK"
        attributes = ""
        if state.required_attributes:
            rendered = ", ".join(f"{key}: {value}" for key, value in sorted(state.required_attributes.items()))
            attributes = f" Required attributes: {rendered}."
        head = (
            f"Here's the goal: buy {state.quantity} × {state.product_query} ({cond}, {match}) for at most "
            f"{format_cents(state.max_total_eur_cents or 0)} in total including shipping and tax, "
            f"delivered to Finland.{attributes}"
        )
    else:
        parts = []
        for i in state.items:
            req = f" ({', '.join(i.requirements)})" if i.requirements else ""
            parts.append(f"{_qty(i)} {i.name}{req}")
        head = (
            f"Here's the goal: every month, find {'; '.join(parts)} for at most "
            f"{format_cents(state.max_total_eur_cents or 0)} including shipping and tax "
            f"(you spend {format_cents(state.current_total_eur_cents or 0)} now), delivered to Finland, "
            f"from at most {state.max_distinct_sellers} sellers."
        )
    return f"{head} {mon} Shall I set it up like this? You can also change anything."


def question_for(field_name: str, state: WorkingState) -> tuple[str, list[str]]:
    """Return (question, fields the question covers). One blocking topic per turn."""
    if field_name == "supported_goal_type":
        if state.goal_type == "unsupported":
            return (
                f"{state.unsupported_reason} can't be tracked yet. I can watch for a product you want "
                "to buy, or for monthly supplies for your business. What would you like to track?",
                [field_name],
            )
        return (
            "What would you like GoalPulse to watch for: a product to buy, or monthly supplies "
            "for your business?",
            [field_name],
        )
    if field_name == "product_query":
        return "Which product are you looking for? A model name helps.", [field_name]
    if field_name == "max_total_eur_cents":
        if state.goal_type == "recurring_procurement":
            return (
                "What's the most you want to spend per month in euros, or how much do you want to save?",
                [field_name],
            )
        return "What's the most you want to pay in euros, in total?", [field_name]
    if field_name in ("include_shipping_and_tax", "delivery_country"):
        budget = (
            f"your {format_cents(state.max_total_eur_cents)} budget"
            if state.max_total_eur_cents is not None
            else "the budget"
        )
        return (
            f"Should {budget} include shipping and taxes, with delivery to Finland?",
            ["include_shipping_and_tax", "delivery_country"],
        )
    if field_name == "product_condition":
        return "Does it have to be new, or is used/refurbished fine too?", [field_name]
    if field_name == "match_policy":
        return (
            f"Should I look for exactly {state.product_query}, or are equivalent models OK?",
            [field_name],
        )
    if field_name == "quantity":
        return "How many do you need?", [field_name]
    if field_name == "items":
        return (
            "Which items do you buy each month, with quantities? For example: 20 L milk, 10 kg coffee beans.",
            [field_name],
        )
    if field_name == "items.quantity_unit":
        names = ", ".join(i.name for i in state.items if i.quantity is None or i.unit is None)
        return f"How much of {names} do you need per month, in pieces, kg or litres?", [field_name]
    if field_name == "items.requirements":
        names = ", ".join(i.name for i in state.items if i.requirements is None)
        return (
            f"Any quality requirements for {names} (for example organic, lactose-free, whole beans)? "
            "Say 'none' if not.",
            [field_name],
        )
    if field_name == "current_total_eur_cents":
        return "Roughly how much do you spend on these per month now, in euros?", [field_name]
    if field_name == "summary_approval":
        return summary_text(state), [field_name]
    return f"Could you tell me more about {field_name}?", [field_name]


# --------------------------------------------------------------------------- candidate


def _qty_number(raw: str) -> int | float:
    value = Decimal(raw)
    return int(value) if value == value.to_integral_value() else float(value)


def _title(state: WorkingState) -> str:
    budget = format_cents(state.max_total_eur_cents) if state.max_total_eur_cents is not None else None
    if state.goal_type == "product_purchase":
        qty = f"{state.quantity} × " if state.quantity and state.quantity > 1 else ""
        title = f"Buy {qty}{state.product_query or 'product'}"
        if not budget:
            return title
        delivered = " delivered" if state.include_shipping_and_tax and state.delivery_country else ""
        return f"{title} at or under {budget}{delivered}"
    title = "Monthly supplies"
    return f"{title} under {budget}" if budget else title


def build_candidate(state: WorkingState) -> dict[str, Any]:
    """GoalSpec-shaped candidate without server-managed fields (GM adds those)."""
    if state.goal_type not in SUPPORTED:
        return {"unsupported_request": state.unsupported_reason} if state.unsupported_reason else {}

    criteria: dict[str, Any] = {"kind": state.goal_type}
    if state.goal_type == "product_purchase":
        product: dict[str, Any] = {}
        if state.product_query:
            product["query"] = state.product_query
        if state.match_policy:
            product["match_policy"] = state.match_policy
        if state.condition:
            product["condition"] = state.condition
        if state.required_attributes:
            product["required_attributes"] = dict(state.required_attributes)
        criteria["product"] = product
        if state.quantity is not None:
            criteria["quantity"] = state.quantity
    else:
        items = []
        for i in state.items:
            item: dict[str, Any] = {"name": i.name}
            if i.quantity is not None:
                item["quantity"] = _qty_number(i.quantity)
            if i.unit is not None:
                item["unit"] = i.unit
            if i.requirements is not None:
                item["requirements"] = list(i.requirements)
            items.append(item)
        criteria["items"] = items
        criteria["cadence"] = "monthly"
        if state.current_total_eur_cents is not None:
            criteria["current_total_eur_cents"] = state.current_total_eur_cents
        if state.max_distinct_sellers is not None:
            criteria["max_distinct_sellers"] = state.max_distinct_sellers
    if state.max_total_eur_cents is not None:
        criteria["max_total_eur_cents"] = state.max_total_eur_cents
    if state.delivery_country:
        criteria["delivery_country"] = state.delivery_country
    if state.include_shipping_and_tax:
        criteria["include_shipping_and_tax"] = True

    candidate: dict[str, Any] = {"title": _title(state), "goal_type": state.goal_type, "criteria": criteria}
    if state.frequency_minutes is not None and state.max_evidence_age_minutes is not None:
        candidate["monitoring"] = {
            "frequency_minutes": state.frequency_minutes,
            "max_evidence_age_minutes": state.max_evidence_age_minutes,
        }
    candidate["actions"] = {
        "notify_user": bool(state.notify_user) if state.notify_user is not None else True,
        "agent_handoff": "prepare_only",
        "auto_purchase": False,
    }
    return candidate


# --------------------------------------------------------------------------- compile


def compile_turn(
    prev: WorkingState,
    patch: ExtractionPatch,
    extra_notices: Sequence[str] = (),
    *,
    withdraw_approval: bool = False,
) -> CompileResult:
    """``withdraw_approval`` is set when the user's message could not be processed: the
    draft must not stay confirmable while a change the user just asked for was dropped."""
    state, notices = apply_patch(prev, patch)
    notices = [*extra_notices, *notices]
    if withdraw_approval:
        state.summary_approved = False
    _fill_proposals(state)
    missing = missing_fields(state)
    candidate = build_candidate(state)

    if not missing:
        errors = goal_spec_errors(candidate)
        if errors:
            # Never declare readiness for something GM would reject; fail closed.
            state.summary_approved = False
            missing = ["summary_approval"]
            notices.append("Something in this draft didn't pass validation, so please review it again.")

    ready = not missing
    if ready:
        state.asked_fields, state.last_question = [], None
        question = None
    else:
        question, covered = question_for(missing[0], state)
        state.asked_fields, state.last_question = covered, question
        if notices:
            question = " ".join(notices + [question])
    return CompileResult(state, candidate, missing, question, ready, notices)
