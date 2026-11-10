"""Checkpoint overlap from the BC230 guide, section 7.2.

The next run reads systemRowVersion values greater than previousRowVersion,
which is the row version committed by the run before the latest one.
"""

from __future__ import annotations


def advance_checkpoint(
    committed: dict | None,
    seen: list[int],
    high_water_before: int | None,
) -> dict:
    seen_max = max(seen) if seen else None
    current = committed or {}
    row_version = current.get("rowVersion")
    if row_version in (None, ""):
        # No systemRowVersion on this API (high_water probe returned None) and no
        # versions seen on the page → keep a posting-date checkpoint only.
        if high_water_before is None and seen_max is None:
            return {
                key: value
                for key, value in current.items()
                if key not in {"rowVersion", "previousRowVersion"}
            }
        base = 0 if high_water_before is None else int(high_water_before)
        row = base if seen_max is None else max(base, int(seen_max))
        return {"previousRowVersion": str(base), "rowVersion": str(row)}
    old_row = int(row_version)
    row = old_row if seen_max is None else max(old_row, int(seen_max))
    return {"previousRowVersion": str(old_row), "rowVersion": str(row)}
