"""Bank feeds that are not called in this slice.

Mono coverage, price, and corporate-account behaviour are unsigned.
No sample statement has been supplied. Neither path stores a balance.
"""

from __future__ import annotations


class FeedClosed(Exception):
    pass


class MonoClient:
    """Interface for a later Mono link. This class does not open a network connection."""

    route = "MONO"

    def pull_balances(self, provider_account_key: str) -> None:
        raise FeedClosed(
            "Mono is not called. Coverage, price, and corporate-account behaviour are unsigned. No balance is stored."
        )

    def pull_transactions(self, provider_account_key: str) -> None:
        raise FeedClosed(
            "Mono is not called. No transactions are stored."
        )


class StatementFileDrop:
    """Interface for a later statement-file route. This class does not parse a file."""

    route = "STATEMENT_FILE"

    def accept(self, filename: str, content: bytes) -> None:
        raise FeedClosed(
            "Statement-file drop is closed. No account is on that route, and no sample statement has been supplied. No balance is stored."
        )
