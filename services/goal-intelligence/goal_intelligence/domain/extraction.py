"""Typed patch that any extractor (LLM or fixture) may propose for one user turn.

The patch is *untrusted*: it only carries facts the user stated. The compiler
decides what is applied, what is rejected and what is asked next. Fields that
are not declared here (owner, state, revision, auto_purchase=true, event names,
...) are rejected by ``extra="forbid"``, so a prompt-injected model cannot set
them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Classification = Literal["product_purchase", "recurring_procurement", "unsupported", "unclear"]
Unit = Literal["pcs", "kg", "L"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ItemPatch(_Strict):
    name: str = Field(min_length=2, max_length=120)
    quantity: str | None = Field(default=None, description="Decimal string, e.g. '20' or '2.5'")
    unit: Unit | None = None
    requirements: list[str] | None = Field(
        default=None, description="None = not stated yet; [] = user said no requirements"
    )


class ExtractionPatch(_Strict):
    classification: Classification | None = None
    unsupported_reason: str | None = Field(default=None, max_length=200)

    # product_purchase
    product_query: str | None = Field(default=None, max_length=200)
    match_policy: Literal["exact_model", "equivalent_allowed"] | None = None
    condition: Literal["new", "any"] | None = None
    quantity: int | None = None
    required_attributes: dict[str, str] | None = None

    # money as decimal strings in EUR; ``currency_seen`` flags non-EUR mentions
    max_total_eur: str | None = Field(default=None, max_length=32)
    current_total_eur: str | None = Field(default=None, max_length=32)
    savings_percent: str | None = Field(default=None, max_length=16)
    currency_seen: str | None = Field(default=None, max_length=8)

    delivery_country: str | None = Field(default=None, max_length=2)
    include_shipping_and_tax: bool | None = None

    # recurring_procurement
    items: list[ItemPatch] | None = Field(default=None, max_length=30)
    item_requirements: dict[str, list[str]] | None = Field(
        default=None, description="Requirements keyed by item name, or '*' for every item"
    )
    max_distinct_sellers: int | None = None

    # monitoring / actions proposals the user explicitly stated or approved
    frequency_minutes: int | None = None
    max_evidence_age_minutes: int | None = None
    notify_user: bool | None = None
    approve_summary: bool | None = Field(
        default=None, description="True only for an explicit yes to the summary question"
    )
    auto_purchase_requested: bool | None = None

    @field_validator("delivery_country")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v

    def is_empty(self) -> bool:
        return not any(v is not None for v in self.model_dump().values())


class ExtractionRequest(_Strict):
    """What an extractor sees: the user's text (as data) and the open question."""

    message: str
    goal_type: Classification | None = None
    asked_fields: list[str] = Field(default_factory=list)
    last_question: str | None = None
    item_names: list[str] = Field(default_factory=list)
