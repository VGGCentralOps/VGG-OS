"""Account numbers are masked before they reach a page or an export."""

from __future__ import annotations


def mask_account_number(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if raw == "":
        return None
    if len(raw) <= 4:
        return "•" * len(raw)
    return ("•" * (len(raw) - 4)) + raw[-4:]
