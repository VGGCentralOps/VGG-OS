"""Decimal handling. Monetary JSON numbers never pass through binary floats."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


class PrecisionError(ValueError):
    pass


def parse_json_exact(text: str) -> Any:
    return json.loads(text, parse_float=Decimal, parse_int=int)


def json_number_to_str(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        raise PrecisionError("Refusing a binary float in a Business Central payload.")
    if isinstance(value, dict):
        return {
            key: json_number_to_str(item)
            for key, item in value.items()
            if not str(key).startswith("@")
        }
    if isinstance(value, list):
        return [json_number_to_str(item) for item in value]
    return value


def as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, bool):
            raise PrecisionError("Boolean is not an amount.")
        elif isinstance(value, float):
            raise PrecisionError("Refusing a binary float amount.")
        else:
            number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PrecisionError("Amount is not a decimal.") from exc
    return number


def fit_money(value: Decimal) -> Decimal:
    """Fit NUMERIC(28,8) only when the value is already exact at that scale."""
    quantised = value.quantize(Decimal("0.00000001"))
    if quantised != value:
        raise PrecisionError("Amount has more than 8 decimal places.")
    if abs(quantised) >= Decimal("1e20"):
        raise PrecisionError("Amount does not fit numeric(28,8).")
    return quantised


def money_text(value: Decimal | None) -> str:
    if value is None:
        return "Missing"
    cents = value.quantize(Decimal("0.01"))
    if cents == value:
        return f"{cents:,.2f}"
    return format(value, ",f")
