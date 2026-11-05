"""Trial balance comparison. A missing side stays missing."""

from __future__ import annotations

from decimal import Decimal


def merge_trial_balance(local: dict[str, dict], control: dict[str, dict]) -> list[dict]:
    rows = []
    for code in sorted(set(local) | set(control)):
        left = local.get(code)
        right = control.get(code)
        local_amount = None if left is None else left.get("amount")
        bc_amount = None if right is None else right.get("amount")
        difference = None
        if local_amount is not None and bc_amount is not None:
            difference = Decimal(local_amount) - Decimal(bc_amount)
        rows.append(
            {
                "code": code,
                "name": (left or {}).get("name") or (right or {}).get("name"),
                "account_type": (left or {}).get("account_type"),
                "local_amount": local_amount,
                "local_debit": None if left is None else left.get("debit"),
                "local_credit": None if left is None else left.get("credit"),
                "local_count": None if left is None else left.get("count"),
                "bc_amount": bc_amount,
                "bc_debit": None if right is None else right.get("debit"),
                "bc_credit": None if right is None else right.get("credit"),
                "bc_count": None if right is None else right.get("entry_count"),
                "difference": difference,
            }
        )
    return rows
