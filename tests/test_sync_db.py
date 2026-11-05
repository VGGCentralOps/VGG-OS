import json
from datetime import date

import httpx
import psycopg
import pytest

from app.constants import ADVANCLY_COMPANY_ID, ADVANCLY_WORKBOOK_NAME, REQUIRED_ENTITY_SETS
from app.settings import Settings, load_settings
from app.sync_service import SyncService

FULL_BANK_NUMBER = "9990001234567890"
LEDGER_NUMBER = "0123456789"


def settings_for(database_url: str, **overrides) -> Settings:
    base = load_settings()
    values = base.__dict__.copy()
    values["database_url"] = database_url
    values.update(overrides)
    return Settings(**values)


def operator_settings():
    current = load_settings()
    if not current.test_database_url:
        pytest.skip("Test database is not configured")
    assert current.test_database_url.rsplit("/", 1)[-1] == "vggos_test"
    return current


def test_uses_the_test_database():
    assert operator_settings().test_database_url.endswith("/vggos_test")


class Scripted(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.urls = []

    def handle_request(self, request):
        self.urls.append(str(request.url))
        assert request.method == "GET"
        return self.handler(request)


def response(payload, status=200):
    return httpx.Response(status, text=json.dumps(payload), headers={"content-type": "application/json"})


def test_refuses_to_call_without_credentials():
    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        raise AssertionError(request.url)

    current = load_settings()
    bare = settings_for(
        current.test_database_url or "postgresql://unused",
        bc_base_url="",
        bc_auth_method="",
        bc_username="",
        bc_access_key="",
    )
    with pytest.raises(Exception):
        SyncService(bare, transport=Scripted(handler)).run_advancly(date(2026, 1, 1), date(2026, 1, 31), 2026, 1)
    assert called["n"] == 0


def test_stops_when_advancly_is_absent():
    current = operator_settings()
    transport = Scripted(lambda request: response({"value": [{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Other Co"}]}))
    outcome = SyncService(
        settings_for(
            current.test_database_url,
            bc_base_url="https://other.example.test/BC230",
            bc_auth_method="basic",
            bc_username="SVC-INT-RO",
            bc_access_key="test-key",
        ),
        transport=transport,
    ).run_advancly(date(2026, 1, 1), date(2026, 1, 31), 2026, 1)
    assert outcome.status == "company_not_found"
    assert len(transport.urls) == 1
    assert "glEntries" not in transport.urls[0]


def test_advancly_load_is_idempotent_and_does_not_invent_a_balance():
    current = operator_settings()
    entries = [
        {
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
            "entryNumber": 1,
            "glAccountNumber": "1000",
            "postingDate": "2026-01-15",
            "documentNumber": "GJ1",
            "amount": 10.5,
            "debitAmount": 10.5,
            "creditAmount": 0,
            "transactionNumber": 9,
            "dimensionSetId": 0,
            "systemRowVersion": 4,
        },
        {
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2",
            "entryNumber": 2,
            "glAccountNumber": "2000",
            "postingDate": "2026-01-15",
            "documentNumber": "GJ1",
            "amount": -10.5,
            "debitAmount": 0,
            "creditAmount": 10.5,
            "transactionNumber": 9,
            "dimensionSetId": 0,
            "systemRowVersion": 5,
        },
    ]
    bad = {
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3",
        "entryNumber": 3,
        "glAccountNumber": "1000",
        "postingDate": "2026-01-16",
        "amount": None,
        "transactionNumber": 10,
        "systemRowVersion": 6,
    }

    def handler(request):
        path = request.url.path
        query = str(request.url)
        if path.endswith("/companies") and "/companies(" not in path:
            return response({"value": [{"id": ADVANCLY_COMPANY_ID, "displayName": ADVANCLY_WORKBOOK_NAME}]})
        if path.endswith("/$metadata") or path.endswith("/%24metadata"):
            body = "".join(f'<EntitySet Name="{name}"/>' for name in REQUIRED_ENTITY_SETS)
            return httpx.Response(200, text=body)
        if path.endswith("/generalLedgerSetups"):
            return response({"value": [{"id": "dddddddd-dddd-dddd-dddd-ddddddddddd1", "lcyCode": "NGN", "systemRowVersion": 1}]})
        if path.endswith("/glAccounts"):
            return response({"value": [
                {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1", "number": "1000", "name": "Cash", "accountType": "Posting", "blocked": False, "systemRowVersion": 2},
                {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2", "number": "2000", "name": "Revenue", "accountType": "Posting", "blocked": False, "systemRowVersion": 2},
            ]})
        if path.endswith("/bankAccounts"):
            return response({"value": [{
                "id": "cccccccc-cccc-cccc-cccc-ccccccccccc1",
                "number": LEDGER_NUMBER,
                "name": "Operating",
                "bankAccountNumber": FULL_BANK_NUMBER,
                "currencyCode": "NGN",
                "blocked": False,
                "systemRowVersion": 2,
            }]})
        if path.endswith("/glEntries") and ("$top=1" in query or "%24top=1" in query):
            return response({"value": [{"systemRowVersion": 3}]})
        if path.endswith("/glEntries"):
            batch = entries if "systemRowVersion" in query else entries + [bad]
            return response({"value": batch})
        if path.endswith("/glBalancesByPeriod"):
            return response({"value": [
                {"glAccountNumber": "1000", "postingYear": 2026, "postingMonth": 1, "amount": 10.5, "debitAmount": 10.5, "creditAmount": 0, "entryCount": 1, "maxEntryNumber": 1},
                {"glAccountNumber": "2000", "postingYear": 2026, "postingMonth": 1, "amount": -10.5, "debitAmount": 0, "creditAmount": 10.5, "entryCount": 1, "maxEntryNumber": 2},
            ]})
        return response({"value": []})

    service_settings = settings_for(
        current.test_database_url,
        bc_base_url="https://bc.example.test/BC230",
        bc_auth_method="basic",
        bc_username="SVC-INT-RO",
        bc_access_key="test-key",
    )
    first = SyncService(service_settings, transport=Scripted(handler)).run_advancly(date(2026, 1, 1), date(2026, 1, 31), 2026, 1)
    assert first.status == "finished", first.message
    second = SyncService(service_settings, transport=Scripted(handler)).run_advancly(date(2026, 1, 1), date(2026, 1, 31), 2026, 1)
    assert second.status == "finished", second.message

    with psycopg.connect(current.test_database_url) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM finance.gl_entry e
            JOIN core.entity n ON n.id = e.entity_id
            WHERE n.code = 'ADVANCLY'
            """
        ).fetchone()[0]
        assert count == 2
        zeroed = conn.execute(
            "SELECT COUNT(*) FROM finance.gl_entry WHERE source_entry_key = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3'"
        ).fetchone()[0]
        assert zeroed == 0
        balances = conn.execute("SELECT COUNT(*) FROM finance.bank_balance_snapshot").fetchone()[0]
        assert balances == 0
        feeds = conn.execute("SELECT COUNT(*) FROM finance.bank_feed_observation").fetchone()[0]
        assert feeds == 0
        tasks = conn.execute(
            "SELECT mismatch_code, COUNT(*) FROM ops.bank_mismatch_task WHERE status = 'OPEN' GROUP BY mismatch_code"
        ).fetchall()
        assert tasks == [("IN_BC_NO_FEED", 1)]
        display = conn.execute("SELECT account_display FROM finance.bank_account").fetchone()[0]
        assert FULL_BANK_NUMBER not in display
        assert LEDGER_NUMBER not in display
        assert display.endswith("6789")
        rule = conn.execute("SELECT enabled, inactive_after_months, dormant_after_months FROM finance.bank_dormancy_rule").fetchone()
        assert rule == (False, None, None)
        failed = conn.execute(
            """
            SELECT committed_checkpoint FROM source.sync_stream
            WHERE endpoint_code = 'glEntries'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()[0]
        assert failed["rowVersion"] == "6" or int(failed["rowVersion"]) >= 5


def test_app_role_cannot_insert_a_balance():
    current = operator_settings()
    migration = current.migration_database_url.rsplit("/", 1)[0] + "/vggos_test"
    with psycopg.connect(migration) as conn:
        assert conn.execute(
            "SELECT has_table_privilege('vggos_app', 'finance.bank_balance_snapshot', 'INSERT')"
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_table_privilege('vggos_app', 'finance.bank_connection', 'INSERT')"
        ).fetchone()[0] is False
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                INSERT INTO finance.bank_dormancy_rule (applies_to, enabled, inactive_after_months, note)
                VALUES ('GROUP_DEFAULT', true, 12, 'not signed')
                """
            )
            conn.commit()
