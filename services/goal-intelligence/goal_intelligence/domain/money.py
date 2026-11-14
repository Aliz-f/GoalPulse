"""Deterministic EUR parsing. Money is integer EUR cents; never binary floats."""

from __future__ import annotations

import re
from decimal import ROUND_DOWN, Decimal, InvalidOperation

_MAX_EUR = Decimal("10000000")  # 10 M EUR: anything above is almost certainly a parse error


class MoneyError(ValueError):
    """Raised when an amount cannot be safely interpreted as EUR."""


def parse_eur_to_cents(raw: str) -> int:
    """Convert a decimal EUR string such as "600", "212.50" or "1 299,90" to cents.

    Accepts a dot or a comma as the decimal separator and spaces/thin spaces or a
    single style of thousands separator. Rejects ambiguous, negative, sub-cent or
    absurdly large values instead of guessing.
    """
    if not isinstance(raw, str):  # floats/ints from an LLM are refused on purpose
        raise MoneyError("amount must be a decimal string")
    text = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    text = text.removeprefix("€").removesuffix("€")
    if not text or text.startswith("-"):
        raise MoneyError("amount must be a positive number")

    if "," in text and "." in text:
        # The right-most separator is the decimal mark: "1.299,90" or "1,299.90".
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", text):
            # "1,299" reads as a thousands separator; the canonical summary shows
            # the interpreted amount and the user must approve it.
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise MoneyError(f"not a plain amount: {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise MoneyError(str(exc)) from exc
    if value > _MAX_EUR:
        raise MoneyError("amount is implausibly large")
    if value != value.quantize(Decimal("0.01")):
        raise MoneyError("amounts smaller than one cent are not allowed")
    return int((value * 100).to_integral_value())


def cents_after_savings(current_cents: int, percent: str) -> int:
    """Target budget after an explicit percentage saving, rounded down to the cent.

    Rounding down keeps the target conservative (never above what the user asked).
    """
    try:
        pct = Decimal(percent.strip().rstrip("%"))
    except InvalidOperation as exc:
        raise MoneyError(f"not a percentage: {percent!r}") from exc
    if not (Decimal(0) < pct < Decimal(100)):
        raise MoneyError("saving percentage must be between 0 and 100")
    target = Decimal(current_cents) * (Decimal(100) - pct) / Decimal(100)
    return int(target.to_integral_value(rounding=ROUND_DOWN))


def format_cents(cents: int) -> str:
    """Human display, e.g. 21250 -> 'EUR 212.50'. Display only; never parse back."""
    return f"EUR {Decimal(cents) / 100:.2f}"
