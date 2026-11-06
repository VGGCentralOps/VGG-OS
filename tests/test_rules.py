from datetime import date
from decimal import Decimal

import pytest

from app.bc_url import ConfigError, resolve_api_root
from app.checkpoint import advance_checkpoint
from app.feeds import FeedClosed, MonoClient, StatementFileDrop
from app.masking import mask_account_number
from app.mismatch import FeedRef, RegisterAccount, classify_mismatches
from app.money import PrecisionError, fit_money, money_text, parse_json_exact
from app.queries import REGISTER_SQL
from app.redact import redact
from app.sync_service import month_slices
from app.trial_balance import merge_trial_balance
from app.web import health_body


def test_json_numbers_stay_exact():
    payload = parse_json_exact('{"amount": 10.10, "count": 9007199254740993}')
    assert payload["amount"] == Decimal("10.10")
    assert payload["count"] == 9007199254740993
    assert fit_money(payload["amount"]) == Decimal("10.10")


def test_over_precise_amount_is_rejected():
    with pytest.raises(PrecisionError):
        fit_money(Decimal("1.123456789"))


def test_missing_money_is_not_zero():
    assert money_text(None) == "Missing"
    assert money_text(Decimal("0")) == "0.00"


def test_mask_keeps_only_the_last_four():
    assert mask_account_number("0123456789") == "••••••6789"
    assert mask_account_number("123") == "•••"
    assert mask_account_number("  ") is None
    assert mask_account_number(None) is None


def test_checkpoint_overlap():
    first = advance_checkpoint({}, [4, 5], 3)
    assert first == {"previousRowVersion": "3", "rowVersion": "5"}
    second = advance_checkpoint(first, [6], None)
    assert second == {"previousRowVersion": "5", "rowVersion": "6"}


def test_month_slices_cover_the_range():
    slices = list(month_slices(date(2026, 1, 15), date(2026, 3, 2)))
    assert slices == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 2)),
    ]


def test_sample_and_odata_urls_are_refused():
    with pytest.raises(ConfigError):
        resolve_api_root("http://10.250.50.9:7148/BC230/api/vgg/integration/v1.0")
    with pytest.raises(ConfigError):
        resolve_api_root("https://bc.example.test/BC230/ODataV4")
    with pytest.raises(ConfigError):
        resolve_api_root("https://bc-host.example.local:7048/BC230")
    assert resolve_api_root("https://bc.example.test/BC230") == "https://bc.example.test/BC230/api/vgg/finance/v1.0"
    assert (
        resolve_api_root("https://10.250.50.9:7149/BC230", "/api/vgg/integration/v1.0")
        == "https://10.250.50.9:7149/BC230/api/vgg/integration/v1.0"
    )


def test_merge_does_not_invent_zero():
    rows = merge_trial_balance(
        {"1000": {"name": "Cash", "amount": Decimal("10.00"), "count": 1}},
        {"2000": {"amount": Decimal("5.00"), "entry_count": 2}},
    )
    by_code = {row["code"]: row for row in rows}
    assert by_code["1000"]["bc_amount"] is None
    assert by_code["2000"]["local_amount"] is None
    assert by_code["1000"]["difference"] is None
    matched = merge_trial_balance(
        {"1000": {"amount": Decimal("0.00")}},
        {"1000": {"amount": Decimal("0.00")}},
    )
    assert matched[0]["difference"] == 0
    assert matched[0]["local_amount"] == 0


def test_three_mismatch_states_and_no_dormancy():
    tasks = classify_mismatches(
        [RegisterAccount(1, "bc-1", True, "NO_FEED"), RegisterAccount(2, "bc-2", False, "NO_FEED")],
        [],
    )
    assert [task.mismatch_code for task in tasks] == ["IN_BC_NO_FEED"]
    assert tasks[0].assignee_role == "CENTRAL_OPS"
    fed = classify_mismatches(
        [RegisterAccount(1, "bc-1", True, "STALE")],
        [FeedRef("mono-9", None, True)],
    )
    codes = {task.mismatch_code for task in fed}
    assert codes == {"LINKED_STALE", "FEED_WITHOUT_BC"}
    assert "DORMANT" not in codes


def test_feeds_do_not_return_balances():
    with pytest.raises(FeedClosed):
        MonoClient().pull_balances("account")
    with pytest.raises(FeedClosed):
        StatementFileDrop().accept("statement.csv", b"amount,0")


def test_register_query_does_not_read_secrets():
    folded = REGISTER_SQL.lower()
    for banned in ("bank_account_secret", "bank_account_number", "iban", "payload", "raw_record"):
        assert banned not in folded


def test_redact_and_health_do_not_carry_a_secret():
    secret = "super-secret-key"
    assert secret not in redact(f"HTTP 401 {secret}", [secret])
    body = health_body("up", "not_configured")
    assert secret not in str(body)
    assert set(body) == {"service", "database", "business_central", "sql_pack"}
