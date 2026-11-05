"""The three bank-account mismatch states. Dormancy is not one of them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisterAccount:
    bank_account_id: int
    bc_account_id: str
    in_latest_bc_list: bool
    link_status: str


@dataclass(frozen=True)
class FeedRef:
    feed_reference: str
    bc_account_id: str | None
    stale: bool


@dataclass(frozen=True)
class MismatchTask:
    mismatch_code: str
    assignee_role: str
    bank_account_id: int | None
    feed_reference: str | None
    detail: str


def classify_mismatches(accounts: list[RegisterAccount], feeds: list[FeedRef]) -> list[MismatchTask]:
    tasks: list[MismatchTask] = []
    live_ids = {account.bc_account_id for account in accounts if account.in_latest_bc_list}
    for account in accounts:
        if not account.in_latest_bc_list:
            continue
        if account.link_status == "STALE":
            tasks.append(
                MismatchTask(
                    "LINKED_STALE",
                    "ACCOUNT_OWNER",
                    account.bank_account_id,
                    None,
                    "The feed is linked, but it is stale. The account owner re-links it or uploads the missing statement.",
                )
            )
        elif account.link_status == "LINKED":
            continue
        else:
            tasks.append(
                MismatchTask(
                    "IN_BC_NO_FEED",
                    "CENTRAL_OPS",
                    account.bank_account_id,
                    None,
                    "This account is in Business Central and no feed is connected. Central Operations links it or assigns a statement-file route.",
                )
            )
    for feed in feeds:
        if feed.bc_account_id not in live_ids:
            tasks.append(
                MismatchTask(
                    "FEED_WITHOUT_BC",
                    "TREASURY_AND_INTERNAL_AUDIT",
                    None,
                    feed.feed_reference,
                    "A feed identifier has no Business Central bank account. Treasury and Internal Audit follow it up.",
                )
            )
    return tasks
