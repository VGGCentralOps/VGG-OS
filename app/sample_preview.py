"""Labeled screen samples. These rows are not written to the ledger or the register."""

from __future__ import annotations


def trial_rows() -> list[dict]:
    return [
        {
            "code": "1000",
            "name": "Cash",
            "local_amount": "10.50",
            "bc_amount": "10.50",
            "difference": "0.00",
            "local_text": "10.50",
            "bc_text": "10.50",
            "difference_text": "0.00",
            "local_count": 1,
            "bc_count": 1,
        },
        {
            "code": "2000",
            "name": "Revenue",
            "local_amount": "-10.50",
            "bc_amount": None,
            "difference": None,
            "local_text": "-10.50",
            "bc_text": "Missing",
            "difference_text": "Not comparable",
            "local_count": 1,
            "bc_count": None,
        },
    ]


def bank_rows() -> list[dict]:
    return [
        {
            "entity_name": "Advancly Technologies NIG Ltd.",
            "account_display": "••••6789",
            "bank_name": "Operating",
            "currency_code": "NGN",
            "bc_account_id": "sample-bank-0001",
            "accountable_owner": None,
            "data_route": "UNASSIGNED",
            "link_status": "NO_FEED",
            "in_latest_bc_list": True,
            "active": True,
        },
        {
            "entity_name": "Advancly Technologies NIG Ltd.",
            "account_display": "••••4410",
            "bank_name": "Collections",
            "currency_code": "NGN",
            "bc_account_id": "sample-bank-0002",
            "accountable_owner": None,
            "data_route": "UNASSIGNED",
            "link_status": "NO_FEED",
            "in_latest_bc_list": True,
            "active": True,
        },
        {
            "entity_name": "Advancly Technologies NIG Ltd.",
            "account_display": "••••2201",
            "bank_name": "USD reserve",
            "currency_code": "USD",
            "bc_account_id": "sample-bank-0003",
            "accountable_owner": None,
            "data_route": "UNASSIGNED",
            "link_status": "NO_FEED",
            "in_latest_bc_list": True,
            "active": True,
        },
    ]


def sync_runs() -> list[dict]:
    return [
        {
            "id": "S1",
            "endpoint_code": "glAccounts",
            "status": "SUCCEEDED",
            "rows_read": 2,
            "rows_accepted": 2,
            "rows_rejected": 0,
            "committed_checkpoint": "sample",
            "error_summary": "",
        },
        {
            "id": "S2",
            "endpoint_code": "glEntries",
            "status": "FAILED",
            "rows_read": 0,
            "rows_accepted": 0,
            "rows_rejected": 0,
            "committed_checkpoint": "unchanged",
            "error_summary": "Sample failure. The checkpoint stays where it was.",
        },
    ]
