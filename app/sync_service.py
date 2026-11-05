"""Read-only Advancly sync into the v0.3 database.

A run commits its checkpoint only after its pages are stored and the normalised
rows commit. A failure leaves the previous checkpoint in place.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlparse

from psycopg.types.json import Json

from app.bc_client import BcClient, BcError
from app.bc_url import ConfigError
from app.checkpoint import advance_checkpoint
from app.constants import (
    ADVANCLY_COMPANY_ID,
    ADVANCLY_ENTITY_CODE,
    ADVANCLY_WORKBOOK_NAME,
    DEV_ISSUER,
    DEV_SUBJECT,
    MASTER_STREAMS,
    REQUIRED_ENTITY_SETS,
)
from app.db import connect
from app.masking import mask_account_number
from app.mismatch import FeedRef, RegisterAccount, classify_mismatches
from app.money import PrecisionError, as_decimal, fit_money, json_number_to_str
from app.redact import redact
from app.settings import Settings
from app.trial_balance import merge_trial_balance

log = logging.getLogger("vggos.sync")
LOCK_KEY = 760061
CURRENCY = re.compile(r"^[A-Z]{3}$")
GL_SELECT = (
    "id,entryNumber,glAccountNumber,postingDate,documentNumber,amount,debitAmount,"
    "creditAmount,transactionNumber,dimensionSetId,globalDimension1Code,"
    "globalDimension2Code,systemRowVersion,lastModifiedDateTime"
)


class SyncFailure(Exception):
    pass


@dataclass
class SyncOutcome:
    status: str
    message: str
    companies: list[dict] = field(default_factory=list)


def month_slices(start: date, end: date):
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        last_day = monthrange(cursor.year, cursor.month)[1]
        slice_end = date(cursor.year, cursor.month, last_day)
        yield max(start, cursor), min(end, slice_end)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def scope_hash(scope: dict) -> str:
    raw = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def instance_code(api_root: str) -> str:
    parsed = urlparse(api_root)
    prefix = parsed.path
    marker = "/api/vgg/finance/v1.0"
    if prefix.endswith(marker):
        prefix = prefix[: -len(marker)]
    label = f"{(parsed.hostname or '').lower()}:{parsed.port or 443}{prefix.lower()}"
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
    safe = re.sub(r"[^a-z0-9]+", "-", label).strip("-")[:40]
    return f"bc-{safe}-{digest}"


def company_label(row: dict) -> str | None:
    for key in ("displayName", "name", "Name"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return None


def blank_date(value) -> str | None:
    if value in (None, "", "0001-01-01"):
        return None
    return str(value)


def blank_text(value) -> str:
    if value in (None, "", " ", "_x0020_"):
        return ""
    return str(value).replace("_x0020_", " ").strip()


def as_int(value):
    if value is None or value == "":
        return None
    return int(str(value))


def payload_sha(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_identity(entity_set: str, payload: dict) -> str | None:
    if payload.get("id"):
        return str(payload["id"])
    if entity_set == "glBalancesByPeriod":
        parts = [payload.get("glAccountNumber"), payload.get("postingYear"), payload.get("postingMonth")]
        if all(part not in (None, "") for part in parts):
            return "|".join(str(part) for part in parts)
    return None


class SyncService:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport

    def run_advancly(
        self,
        date_from: date,
        date_to: date,
        year: int,
        month: int,
        actor_id: int | None = None,
    ) -> SyncOutcome:
        api_root = self.settings.require_bc()
        if date_from > date_to:
            raise ConfigError("The posting-date range is reversed. No request was sent.")
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        if month_end < date_from or month_start > date_to:
            raise ConfigError("The reconciliation month is outside the posting-date range. No request was sent.")

        client = None
        conn = None
        locked = False
        try:
            client = BcClient(
                self.settings.bc_base_url,
                self.settings.bc_username,
                self.settings.bc_access_key,
                transport=self.transport,
            )
            conn = connect(self.settings.database_url)
            conn.autocommit = True
            conn.execute("SET statement_timeout = '15min'")
            locked_row = conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (LOCK_KEY,)).fetchone()
            if not locked_row["locked"]:
                return SyncOutcome("already_running", "A books sync is already running.")
            locked = True
            actor = actor_id if actor_id is not None else self._actor(conn)
            instance = self._upsert_instance(conn, api_root)
            outcome = self._match_company(conn, client, instance)
            if outcome is not None:
                return outcome
            company = self._load_company(conn, instance["id"])
            self._require_metadata(client)
            for entity_set in MASTER_STREAMS:
                self._sync_master(conn, client, company, actor, entity_set)
            self._sync_gl(conn, client, company, actor, date_from, date_to)
            self._sync_balances(conn, client, company, actor, year, month)
            with conn.transaction():
                self.refresh_tasks(conn, company["entity_id"])
            self._notice(conn, "INFO", "The first-company sync finished. Check the run list and the trial balance.")
            return SyncOutcome("finished", "The first-company sync finished.")
        except (ConfigError, BcError, SyncFailure, PrecisionError) as exc:
            message = redact(str(exc), self.settings.secret_values())
            try:
                self._notice(conn, "ERROR", message)
            except Exception:
                log.warning("Could not store the sync notice.")
            return SyncOutcome("failed", message)
        finally:
            if locked and conn is not None:
                try:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                except Exception:
                    log.warning("Could not release the sync lock.")
            if conn is not None:
                conn.close()
            if client is not None:
                client.close()

    def refresh_tasks(self, conn, entity_id: int) -> None:
        accounts = [
            RegisterAccount(
                bank_account_id=row["bank_account_id"],
                bc_account_id=row["bc_account_id"],
                in_latest_bc_list=row["in_latest_bc_list"],
                link_status=row["link_status"],
            )
            for row in conn.execute(
                """
                SELECT g.bank_account_id, g.bc_account_id, g.in_latest_bc_list, g.link_status
                FROM finance.bank_account_governance g
                JOIN finance.bank_account a ON a.id = g.bank_account_id
                WHERE a.entity_id = %s
                """,
                (entity_id,),
            ).fetchall()
        ]
        feeds = [
            FeedRef(row["provider_account_key"], row["bc_account_id"], row["stale"])
            for row in conn.execute(
                "SELECT provider_account_key, bc_account_id, stale FROM finance.bank_feed_observation"
            ).fetchall()
        ]
        desired = classify_mismatches(accounts, feeds)
        desired_keys = {
            (task.mismatch_code, task.bank_account_id, task.feed_reference): task for task in desired
        }
        open_rows = conn.execute(
            """
            SELECT id, mismatch_code, bank_account_id, feed_reference
            FROM ops.bank_mismatch_task
            WHERE status = 'OPEN'
            """
        ).fetchall()
        for row in open_rows:
            key = (row["mismatch_code"], row["bank_account_id"], row["feed_reference"])
            if key not in desired_keys:
                conn.execute(
                    """
                    UPDATE ops.bank_mismatch_task
                    SET status = 'CLOSED', closed_at = now()
                    WHERE id = %s AND status = 'OPEN'
                    """,
                    (row["id"],),
                )
        open_keys = {(row["mismatch_code"], row["bank_account_id"], row["feed_reference"]) for row in open_rows}
        for key, task in desired_keys.items():
            if key in open_keys:
                continue
            conn.execute(
                """
                INSERT INTO ops.bank_mismatch_task
                  (mismatch_code, assignee_role, bank_account_id, feed_reference, detail)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (task.mismatch_code, task.assignee_role, task.bank_account_id, task.feed_reference, task.detail),
            )

    def _match_company(self, conn, client: BcClient, instance: dict) -> SyncOutcome | None:
        data = client.get_json(f"{client.api_root}/companies")
        rows = data.get("value")
        if not isinstance(rows, list):
            raise SyncFailure("The companies response has no value array. No company was selected.")
        listed = []
        matched = None
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            item = {"id": str(row["id"]), "name": company_label(row)}
            listed.append(item)
            if str(row["id"]).lower() == ADVANCLY_COMPANY_ID:
                matched = row
        if matched is None:
            message = (
                f"Company id {ADVANCLY_COMPANY_ID} was not in this Business Central instance. "
                "No other company was selected."
            )
            with conn.transaction():
                self._probe(conn, instance["id"], False, listed, message)
                conn.execute(
                    "UPDATE source.instance SET active = false WHERE id = %s",
                    (instance["id"],),
                )
            return SyncOutcome("company_not_found", message, listed)
        label = company_label(matched) or ADVANCLY_WORKBOOK_NAME
        name_note = ""
        if not company_label(matched):
            name_note = " Business Central sent no display name, so the sandbox workbook name is shown."
        with conn.transaction():
            entity_id = self._ensure_entity(conn, label)
            company_id = conn.execute(
                """
                INSERT INTO source.company (instance_id, external_company_key, entity_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (instance_id, external_company_key)
                DO UPDATE SET entity_id = EXCLUDED.entity_id
                RETURNING id
                """,
                (instance["id"], ADVANCLY_COMPANY_ID, entity_id),
            ).fetchone()["id"]
            conn.execute("UPDATE source.instance SET active = true WHERE id = %s", (instance["id"],))
            self._probe(
                conn,
                instance["id"],
                True,
                listed,
                f"Matched Advancly {ADVANCLY_COMPANY_ID}. Business Central name: {label}.{name_note}",
            )
        self._company_id = company_id
        return None

    def _require_metadata(self, client: BcClient) -> None:
        text = client.get_text(f"{client.api_root}/$metadata")
        missing = [name for name in REQUIRED_ENTITY_SETS if f'"{name}"' not in text and f">{name}<" not in text]
        if missing:
            raise SyncFailure(
                "Metadata is missing entity sets: " + ", ".join(missing) + ". No ledger was loaded."
            )

    def _sync_master(self, conn, client, company, actor, entity_set: str) -> None:
        scope = {"endpoint": entity_set, "mode": "full"}
        versions: list[int] = []
        run_id, before = self._begin_run(conn, company["id"], entity_set, scope, actor)
        try:
            self._fetch(conn, client, company, run_id, entity_set, None, versions, None)
            after = advance_checkpoint(before, versions, None)
            with conn.transaction():
                read, accepted, rejected = self._project_master(conn, company, run_id, entity_set)
                self._complete(conn, run_id, actor, after, read, accepted, rejected)
        except Exception as exc:
            self._fail(conn, run_id, exc)
            raise SyncFailure(f"{entity_set} failed. {exc}") from exc

    def _sync_gl(self, conn, client, company, actor, date_from: date, date_to: date) -> None:
        scope = {
            "endpoint": "glEntries",
            "postingDateFrom": date_from.isoformat(),
            "postingDateTo": date_to.isoformat(),
        }
        versions: list[int] = []
        run_id, before = self._begin_run(conn, company["id"], "glEntries", scope, actor)
        high_water = None
        try:
            if before.get("rowVersion"):
                lower = before.get("previousRowVersion") or "0"
                self._fetch(
                    conn, client, company, run_id, "glEntries",
                    f"systemRowVersion gt {lower}", versions, GL_SELECT,
                )
            else:
                high_water = self._high_water(client, company)
                for slice_start, slice_end in month_slices(date_from, date_to):
                    filt = (
                        f"postingDate ge {slice_start.isoformat()} and "
                        f"postingDate le {slice_end.isoformat()}"
                    )
                    self._fetch(conn, client, company, run_id, "glEntries", filt, versions, GL_SELECT)
                if high_water is not None:
                    self._fetch(
                        conn, client, company, run_id, "glEntries",
                        f"systemRowVersion gt {high_water}", versions, GL_SELECT,
                    )
            after = advance_checkpoint(before, versions, high_water)
            with conn.transaction():
                read, accepted, rejected = self._project_gl(conn, company, run_id)
                self._flag_unbalanced(conn, company, run_id, date_from, date_to)
                self._complete(conn, run_id, actor, after, read, accepted, rejected)
        except Exception as exc:
            self._fail(conn, run_id, exc)
            raise SyncFailure(f"glEntries failed. {exc}") from exc

    def _sync_balances(self, conn, client, company, actor, year: int, month: int) -> None:
        scope = {"endpoint": "glBalancesByPeriod", "postingYear": year, "postingMonth": month}
        versions: list[int] = []
        run_id, before = self._begin_run(conn, company["id"], "glBalancesByPeriod", scope, actor)
        try:
            filt = f"postingYear eq {year} and postingMonth eq {month}"

            def keep(record: dict) -> bool:
                try:
                    return int(str(record.get("postingYear"))) == year and int(str(record.get("postingMonth"))) == month
                except (TypeError, ValueError):
                    return False

            self._fetch(conn, client, company, run_id, "glBalancesByPeriod", filt, versions, None, keep, allow_filter_retry=True)
            after = advance_checkpoint(before, versions, None)
            with conn.transaction():
                read, accepted, rejected = self._project_balances(conn, company, run_id, year, month)
                self._flag_control_differences(conn, company, run_id, year, month)
                self._complete(conn, run_id, actor, after, read, accepted, rejected)
        except Exception as exc:
            self._fail(conn, run_id, exc)
            raise SyncFailure(f"glBalancesByPeriod failed. {exc}") from exc

    def _fetch(self, conn, client, company, run_id, entity_set, odata_filter, versions, select, predicate=None, allow_filter_retry=False):
        url = f"{client.api_root}/companies({company['external_company_key']})/{entity_set}"
        params = {}
        if odata_filter:
            params["$filter"] = odata_filter
        if select:
            params["$select"] = select
        try:
            pages = client.iter_values(url, params or None)
            first = True
            for page in pages:
                first = False
                self._store_page(conn, company, run_id, entity_set, page, versions, predicate)
            if first:
                return
        except BcError as exc:
            if allow_filter_retry and exc.status == 400 and odata_filter:
                for page in client.iter_values(url, None):
                    self._store_page(conn, company, run_id, entity_set, page, versions, predicate)
                return
            raise

    def _high_water(self, client: BcClient, company: dict):
        url = f"{client.api_root}/companies({company['external_company_key']})/glEntries"
        try:
            data = client.get_json(
                url,
                {"$select": "systemRowVersion", "$orderby": "systemRowVersion desc", "$top": "1"},
            )
        except BcError as exc:
            if exc.status == 400:
                return None
            raise
        values = data.get("value") or []
        if not values:
            return 0
        version = values[0].get("systemRowVersion")
        return 0 if version in (None, "") else int(str(version))

    def _store_page(self, conn, company, run_id, entity_set, page, versions, predicate) -> None:
        with conn.transaction():
            for record in page:
                if not isinstance(record, dict):
                    continue
                if predicate is not None and not predicate(record):
                    continue
                payload = json_number_to_str(record)
                version = as_int(payload.get("systemRowVersion"))
                if version is not None:
                    versions.append(version)
                identity = record_identity(entity_set, payload)
                source_version = str(version) if version is not None else payload_sha(payload)
                if identity is None:
                    identity = "unkeyed-" + payload_sha(payload)[:16]
                record_key = f"{company['instance_code']}|{company['external_company_key']}|{entity_set}|{identity}"
                modified = payload.get("lastModifiedDateTime") or None
                modified_at = None
                if modified:
                    try:
                        modified_at = datetime.fromisoformat(str(modified).replace("Z", "+00:00"))
                    except ValueError:
                        modified_at = None
                conn.execute(
                    """
                    INSERT INTO source.raw_record
                      (run_id, record_key, source_version, source_modified_at, payload, payload_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, record_key, source_version) DO NOTHING
                    """,
                    (run_id, record_key, source_version, modified_at, Json(payload), payload_sha(payload)),
                )

    def _project_master(self, conn, company, run_id, entity_set: str):
        raws = self._raws(conn, run_id)
        seen: list[str] = []
        rejected = 0
        lcy = self._lcy(conn, company["id"])
        for raw in raws:
            payload = raw["payload"]
            identity = record_identity(entity_set, payload)
            reason = None
            if identity is None or str(identity).startswith("unkeyed-"):
                reason = "Record has no id."
            if reason:
                rejected += 1
                self._validation(conn, run_id, raw["id"], "UNMAPPED_OR_REJECTED", "WARNING", False, f"{entity_set}: {reason}")
                continue
            business_key = self._business_key(entity_set, payload)
            conn.execute(
                """
                INSERT INTO finance.bc_master_record
                  (company_id, entity_set, record_id, business_key, source_version, source_row_version,
                   payload, source_deleted, raw_record_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, false, %s)
                ON CONFLICT (company_id, entity_set, record_id) DO UPDATE SET
                  business_key = EXCLUDED.business_key,
                  source_version = EXCLUDED.source_version,
                  source_row_version = EXCLUDED.source_row_version,
                  payload = EXCLUDED.payload,
                  source_deleted = false,
                  raw_record_id = EXCLUDED.raw_record_id
                WHERE finance.bc_master_record.source_row_version IS NULL
                   OR EXCLUDED.source_row_version IS NULL
                   OR EXCLUDED.source_row_version >= finance.bc_master_record.source_row_version
                """,
                (
                    company["id"], entity_set, identity, business_key, raw["source_version"],
                    as_int(payload.get("systemRowVersion")), Json(payload), raw["id"],
                ),
            )
            seen.append(identity)
            if entity_set == "glAccounts":
                if not self._project_account(conn, company, run_id, raw, payload):
                    rejected += 1
                    continue
            elif entity_set == "bankAccounts":
                if not self._project_bank(conn, company, run_id, raw, payload, lcy):
                    rejected += 1
                    continue
            elif entity_set == "generalLedgerSetups":
                self._apply_lcy(conn, company, payload)
        if seen:
            conn.execute(
                """
                UPDATE finance.bc_master_record
                SET source_deleted = true
                WHERE company_id = %s AND entity_set = %s AND NOT (record_id = ANY(%s))
                """,
                (company["id"], entity_set, seen),
            )
            if entity_set == "bankAccounts":
                conn.execute(
                    """
                    UPDATE finance.bank_account_governance g
                    SET in_latest_bc_list = false
                    WHERE g.bc_account_id <> ALL(%s)
                      AND g.bank_account_id IN (
                        SELECT bank_account_id FROM finance.bank_book_mapping WHERE company_id = %s
                      )
                    """,
                    (seen, company["id"]),
                )
        read = len(raws)
        return read, read - rejected, rejected

    def _project_account(self, conn, company, run_id, raw, payload) -> bool:
        number = blank_text(payload.get("number"))
        if not number:
            self._validation(conn, run_id, raw["id"], "UNMAPPED_OR_REJECTED", "WARNING", False, "G/L account has no number.")
            return False
        name = blank_text(payload.get("name")) or "(not supplied)"
        blocked = payload.get("blocked") is True
        conn.execute(
            """
            INSERT INTO finance.account
              (entity_id, account_code, account_name, account_type, source_company_id, active)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (entity_id, account_code) DO UPDATE SET
              account_name = EXCLUDED.account_name,
              account_type = EXCLUDED.account_type,
              source_company_id = EXCLUDED.source_company_id,
              active = EXCLUDED.active
            """,
            (
                company["entity_id"], number, name, blank_text(payload.get("accountType")) or None,
                company["id"], not blocked,
            ),
        )
        return True

    def _project_bank(self, conn, company, run_id, raw, payload, lcy) -> bool:
        bc_id = str(payload.get("id") or "")
        number = blank_text(payload.get("number"))
        masked = mask_account_number(number)
        currency = blank_text(payload.get("currencyCode")) or (lcy or "")
        if not bc_id or masked is None:
            self._validation(conn, run_id, raw["id"], "UNMAPPED_OR_REJECTED", "WARNING", False, "Bank account has no id or number.")
            return False
        if not CURRENCY.fullmatch(currency):
            self._validation(
                conn, run_id, raw["id"], "UNMAPPED_OR_REJECTED", "WARNING", False,
                f"Bank account {bc_id} has no currency and no local currency. It was not stored as NGN.",
            )
            return False
        self._ensure_currency(conn, currency)
        name = blank_text(payload.get("name")) or "(not supplied)"
        blocked = payload.get("blocked") is True
        existing = conn.execute(
            """
            SELECT bank_account_id FROM finance.bank_book_mapping
            WHERE company_id = %s AND external_bank_account_key = %s
            """,
            (company["id"], bc_id),
        ).fetchone()
        if existing:
            bank_id = existing["bank_account_id"]
            conn.execute(
                """
                UPDATE finance.bank_account
                SET bank_name = %s, account_display = %s, currency_code = %s, active = %s
                WHERE id = %s
                """,
                (name, masked, currency, not blocked, bank_id),
            )
            conn.execute(
                """
                UPDATE finance.bank_account_governance
                SET in_latest_bc_list = true
                WHERE bank_account_id = %s
                """,
                (bank_id,),
            )
        else:
            bank_id = conn.execute(
                """
                INSERT INTO finance.bank_account (entity_id, code, bank_name, account_display, currency_code, active)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (company["entity_id"], f"bc:{bc_id}", name, masked, currency, not blocked),
            ).fetchone()["id"]
            conn.execute(
                """
                INSERT INTO finance.bank_book_mapping
                  (bank_account_id, entity_id, company_id, external_bank_account_key)
                VALUES (%s, %s, %s, %s)
                """,
                (bank_id, company["entity_id"], company["id"], bc_id),
            )
            conn.execute(
                """
                INSERT INTO finance.bank_account_governance
                  (bank_account_id, bc_account_id, accountable_owner, data_route, link_status, in_latest_bc_list)
                VALUES (%s, %s, NULL, 'UNASSIGNED', 'NO_FEED', true)
                """,
                (bank_id, bc_id),
            )
        bank_number = blank_text(payload.get("bankAccountNumber")) or None
        iban = blank_text(payload.get("iban")) or None
        conn.execute(
            """
            INSERT INTO finance.bank_account_secret (bank_account_id, bank_account_number, iban)
            VALUES (%s, %s, %s)
            ON CONFLICT (bank_account_id) DO UPDATE SET
              bank_account_number = EXCLUDED.bank_account_number,
              iban = EXCLUDED.iban
            """,
            (bank_id, bank_number, iban),
        )
        return True

    def _project_gl(self, conn, company, run_id):
        lcy = self._lcy(conn, company["id"])
        if not lcy or not CURRENCY.fullmatch(lcy):
            raise SyncFailure("generalLedgerSetups has no local currency. Ledger amounts were not stored as zero.")
        self._ensure_currency(conn, lcy)
        conn.execute(
            """
            UPDATE core.entity SET functional_currency = %s
            WHERE id = %s AND functional_currency IS NULL
            """,
            (lcy, company["entity_id"]),
        )
        accounts = {
            row["account_code"]: row["id"]
            for row in conn.execute(
                "SELECT id, account_code FROM finance.account WHERE entity_id = %s",
                (company["entity_id"],),
            ).fetchall()
        }
        raws = self._raws(conn, run_id)
        rejected = 0
        for raw in raws:
            payload = raw["payload"]
            reason = self._gl_rejection(payload, accounts)
            if reason:
                rejected += 1
                self._validation(conn, run_id, raw["id"], "UNMAPPED_OR_REJECTED", "WARNING", False, reason)
                continue
            amount = fit_money(as_decimal(payload.get("amount")))
            debit = as_decimal(payload.get("debitAmount"))
            credit = as_decimal(payload.get("creditAmount"))
            debit = None if debit is None else fit_money(debit)
            credit = None if credit is None else fit_money(credit)
            dimensions = {
                "dimensionSetId": payload.get("dimensionSetId"),
                "globalDimension1Code": payload.get("globalDimension1Code") or "",
                "globalDimension2Code": payload.get("globalDimension2Code") or "",
            }
            conn.execute(
                """
                INSERT INTO finance.gl_entry (
                  company_id, entity_id, source_entry_key, source_version, source_row_version,
                  account_id, posting_date, document_reference, currency_code, amount,
                  debit_amount, credit_amount, transaction_number, entry_number, dimension_set_id,
                  dimensions, raw_record_id, source_deleted
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s,
                  %s, %s, false
                )
                ON CONFLICT (company_id, source_entry_key) DO UPDATE SET
                  source_version = EXCLUDED.source_version,
                  source_row_version = EXCLUDED.source_row_version,
                  account_id = EXCLUDED.account_id,
                  posting_date = EXCLUDED.posting_date,
                  document_reference = EXCLUDED.document_reference,
                  currency_code = EXCLUDED.currency_code,
                  amount = EXCLUDED.amount,
                  debit_amount = EXCLUDED.debit_amount,
                  credit_amount = EXCLUDED.credit_amount,
                  transaction_number = EXCLUDED.transaction_number,
                  entry_number = EXCLUDED.entry_number,
                  dimension_set_id = EXCLUDED.dimension_set_id,
                  dimensions = EXCLUDED.dimensions,
                  raw_record_id = EXCLUDED.raw_record_id,
                  source_deleted = false
                WHERE finance.gl_entry.source_row_version IS NULL
                   OR EXCLUDED.source_row_version IS NULL
                   OR EXCLUDED.source_row_version >= finance.gl_entry.source_row_version
                """,
                (
                    company["id"], company["entity_id"], str(payload["id"]), raw["source_version"],
                    as_int(payload.get("systemRowVersion")), accounts[blank_text(payload.get("glAccountNumber"))],
                    blank_date(payload.get("postingDate")), blank_text(payload.get("documentNumber")) or None,
                    lcy, amount, debit, credit, as_int(payload.get("transactionNumber")),
                    as_int(payload.get("entryNumber")), as_int(payload.get("dimensionSetId")),
                    Json(dimensions), raw["id"],
                ),
            )
        read = len(raws)
        return read, read - rejected, rejected

    def _gl_rejection(self, payload: dict, accounts: dict) -> str | None:
        if not payload.get("id") or str(payload.get("id")).startswith("unkeyed-"):
            return "G/L entry has no id."
        try:
            amount = as_decimal(payload.get("amount"))
            if amount is None:
                return f"G/L entry {payload.get('id')} has no amount. It was not stored as zero."
            fit_money(amount)
        except PrecisionError as exc:
            return f"G/L entry {payload.get('id')} amount was rejected: {exc}"
        if blank_date(payload.get("postingDate")) is None:
            return f"G/L entry {payload.get('id')} has no posting date."
        if as_int(payload.get("transactionNumber")) is None:
            return f"G/L entry {payload.get('id')} has no transaction number."
        account = blank_text(payload.get("glAccountNumber"))
        if account not in accounts:
            return f"G/L entry {payload.get('id')} posts to {account or 'a blank account'}, which is not in the loaded chart."
        return None

    def _project_balances(self, conn, company, run_id, year: int, month: int):
        raws = self._raws(conn, run_id)
        rejected = 0
        kept = []
        for raw in raws:
            payload = raw["payload"]
            account = blank_text(payload.get("glAccountNumber"))
            if not account:
                rejected += 1
                self._validation(conn, run_id, raw["id"], "UNMAPPED_OR_REJECTED", "WARNING", False, "Control total has no account number.")
                continue
            kept.append((account, payload))
        conn.execute(
            """
            DELETE FROM finance.bc_gl_period_balance
            WHERE company_id = %s AND posting_year = %s AND posting_month = %s
            """,
            (company["id"], year, month),
        )
        for account, payload in kept:
                conn.execute(
                    """
                    INSERT INTO finance.bc_gl_period_balance
                      (company_id, gl_account_number, posting_year, posting_month, amount, debit_amount,
                       credit_amount, entry_count, max_entry_number, run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company["id"], account, year, month,
                        self._optional_money(payload.get("amount")),
                        self._optional_money(payload.get("debitAmount")),
                        self._optional_money(payload.get("creditAmount")),
                        as_int(payload.get("entryCount")),
                        as_int(payload.get("maxEntryNumber")),
                        run_id,
                    ),
                )
        read = len(raws)
        return read, read - rejected, rejected

    def _optional_money(self, value):
        number = as_decimal(value)
        if number is None:
            return None
        return fit_money(number)

    def _flag_unbalanced(self, conn, company, run_id, date_from: date, date_to: date) -> None:
        rows = conn.execute(
            """
            SELECT transaction_number, SUM(amount) AS total
            FROM finance.gl_entry
            WHERE company_id = %s AND posting_date BETWEEN %s AND %s AND NOT source_deleted
            GROUP BY transaction_number
            HAVING SUM(amount) <> 0
            """,
            (company["id"], date_from, date_to),
        ).fetchall()
        for row in rows:
            self._validation(
                conn, run_id, None, "TRANSACTION_NOT_BALANCED", "WARNING", False,
                f"Transaction {row['transaction_number']} sums to {row['total']} in the loaded dates, not zero.",
                "0", str(row["total"]),
            )

    def _flag_control_differences(self, conn, company, run_id, year: int, month: int) -> None:
        local = {}
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        for row in conn.execute(
            """
            SELECT a.account_code, a.account_name, a.account_type,
                   SUM(e.amount) AS amount, SUM(e.debit_amount) AS debit, SUM(e.credit_amount) AS credit,
                   COUNT(*) AS count
            FROM finance.gl_entry e
            JOIN finance.account a ON a.id = e.account_id
            WHERE e.company_id = %s AND e.posting_date BETWEEN %s AND %s AND NOT e.source_deleted
            GROUP BY a.account_code, a.account_name, a.account_type
            """,
            (company["id"], start, end),
        ).fetchall():
            local[row["account_code"]] = row
        control = {}
        for row in conn.execute(
            """
            SELECT gl_account_number, amount, debit_amount, credit_amount, entry_count
            FROM finance.bc_gl_period_balance
            WHERE company_id = %s AND posting_year = %s AND posting_month = %s
            """,
            (company["id"], year, month),
        ).fetchall():
            control[row["gl_account_number"]] = {
                "amount": row["amount"],
                "debit": row["debit_amount"],
                "credit": row["credit_amount"],
                "entry_count": row["entry_count"],
            }
        for item in merge_trial_balance(local, control):
            if item["local_amount"] is not None and item["bc_amount"] is not None and item["difference"] != 0:
                self._validation(
                    conn, run_id, None, "TB_CONTROL_DIFFERENCE", "WARNING", False,
                    f"Account {item['code']} local net {item['local_amount']} differs from Business Central {item['bc_amount']}.",
                    str(item["bc_amount"]), str(item["local_amount"]),
                )
            elif item["local_amount"] is None or item["bc_amount"] is None:
                self._validation(
                    conn, run_id, None, "TB_SIDE_MISSING", "WARNING", False,
                    f"Account {item['code']} is missing on one side. The missing side is not zero.",
                )

    def _apply_lcy(self, conn, company, payload) -> None:
        lcy = blank_text(payload.get("lcyCode"))
        if CURRENCY.fullmatch(lcy):
            self._ensure_currency(conn, lcy)
            conn.execute(
                """
                UPDATE core.entity SET functional_currency = %s
                WHERE id = %s AND functional_currency IS NULL
                """,
                (lcy, company["entity_id"]),
            )

    def _lcy(self, conn, company_id: int) -> str | None:
        row = conn.execute(
            """
            SELECT payload->>'lcyCode' AS lcy
            FROM finance.bc_master_record
            WHERE company_id = %s AND entity_set = 'generalLedgerSetups' AND NOT source_deleted
            ORDER BY record_id
            LIMIT 1
            """,
            (company_id,),
        ).fetchone()
        if not row:
            return None
        return blank_text(row["lcy"]) or None

    def _ensure_currency(self, conn, code: str) -> None:
        conn.execute(
            "INSERT INTO core.currency (code, name) VALUES (%s, %s) ON CONFLICT (code) DO NOTHING",
            (code, code),
        )

    def _business_key(self, entity_set: str, payload: dict) -> str | None:
        if entity_set == "glAccounts" or entity_set == "bankAccounts":
            return blank_text(payload.get("number")) or None
        if entity_set == "dimensions":
            return blank_text(payload.get("code")) or None
        if entity_set == "dimensionValues":
            dimension = blank_text(payload.get("dimensionCode"))
            code = blank_text(payload.get("code"))
            return f"{dimension}|{code}" if dimension and code else None
        if entity_set == "accountingPeriods":
            return blank_date(payload.get("startingDate"))
        return payload.get("id")

    def _raws(self, conn, run_id: int) -> list[dict]:
        return conn.execute(
            """
            SELECT id, record_key, source_version, payload
            FROM source.raw_record
            WHERE run_id = %s
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()

    def _begin_run(self, conn, company_id, endpoint: str, scope: dict, actor: int):
        digest = scope_hash(scope)
        with conn.transaction():
            stream = conn.execute(
                """
                INSERT INTO source.sync_stream (company_id, endpoint_code, query_scope, scope_hash)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (company_id, endpoint_code, scope_hash)
                DO UPDATE SET updated_at = now()
                RETURNING id
                """,
                (company_id, endpoint, Json(scope), digest),
            ).fetchone()
            current = conn.execute(
                "SELECT committed_checkpoint FROM source.sync_stream WHERE id = %s FOR UPDATE",
                (stream["id"],),
            ).fetchone()
            before = current["committed_checkpoint"] or {}
            run = conn.execute(
                """
                INSERT INTO source.ingestion_run
                  (stream_id, idempotency_key, status, requested_by, checkpoint_before)
                VALUES (%s, %s, 'RUNNING', %s, %s)
                RETURNING id
                """,
                (stream["id"], str(uuid.uuid4()), actor, Json(before)),
            ).fetchone()
        return run["id"], before

    def _complete(self, conn, run_id, actor, checkpoint, read, accepted, rejected) -> None:
        if read != accepted + rejected:
            raise SyncFailure("Row counts do not add up. The checkpoint was not moved.")
        conn.execute(
            """
            UPDATE source.ingestion_run
            SET status = 'VALIDATED', rows_read = %s, rows_accepted = %s, rows_rejected = %s,
                checkpoint_after = %s
            WHERE id = %s AND status = 'RUNNING'
            """,
            (read, accepted, rejected, Json(checkpoint), run_id),
        )
        conn.execute("SELECT source.complete_run(%s, %s)", (run_id, actor))

    def _fail(self, conn, run_id, exc: Exception) -> None:
        message = redact(str(exc), self.settings.secret_values())
        try:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE source.ingestion_run
                    SET status = 'FAILED', finished_at = clock_timestamp(), error_summary = %s
                    WHERE id = %s AND status = 'RUNNING'
                    """,
                    (message, run_id),
                )
        except Exception:
            log.warning("Could not mark run %s failed.", run_id)

    def _validation(self, conn, run_id, raw_id, rule, severity, passed, message, expected=None, actual=None) -> None:
        conn.execute(
            """
            INSERT INTO source.validation_result
              (run_id, raw_record_id, rule_code, severity, passed, expected_value, actual_value, message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (run_id, raw_id, rule, severity, passed, expected, actual, message),
        )

    def _actor(self, conn) -> int:
        row = conn.execute(
            """
            SELECT p.id
            FROM iam.principal p
            JOIN iam.identity_provider i ON i.id = p.provider_id
            WHERE i.issuer = %s AND p.external_subject = %s
            """,
            (DEV_ISSUER, DEV_SUBJECT),
        ).fetchone()
        if row is None:
            raise SyncFailure("The developer principal is not installed.")
        return row["id"]

    def _upsert_instance(self, conn, api_root: str) -> dict:
        code = instance_code(api_root)
        with conn.transaction():
            system = conn.execute("SELECT id FROM source.system WHERE code = 'BUSINESS_CENTRAL'").fetchone()
            row = conn.execute(
                """
                INSERT INTO source.instance
                  (system_id, code, base_url, credential_secret_reference, auth_method, network_notes, active)
                VALUES (%s, %s, %s, %s, 'basic', %s, false)
                ON CONFLICT (code) DO UPDATE SET
                  base_url = EXCLUDED.base_url,
                  credential_secret_reference = EXCLUDED.credential_secret_reference,
                  auth_method = EXCLUDED.auth_method
                RETURNING id, code
                """,
                (
                    system["id"], code, api_root, self.settings.bc_secret_reference,
                    "HTTPS custom API /api/vgg/finance/v1.0. Do not append that route to /ODataV4/.",
                ),
            ).fetchone()
        return row

    def _ensure_entity(self, conn, label: str) -> int:
        parent = conn.execute("SELECT id FROM core.entity WHERE code = 'VGG_GROUP'").fetchone()["id"]
        row = conn.execute(
            """
            INSERT INTO core.entity (code, name, entity_type, parent_id)
            VALUES (%s, %s, 'LEGAL_ENTITY', %s)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (ADVANCLY_ENTITY_CODE, label, parent),
        ).fetchone()
        return row["id"]

    def _load_company(self, conn, instance_id: int) -> dict:
        row = conn.execute(
            """
            SELECT c.id, c.entity_id, c.external_company_key, i.code AS instance_code
            FROM source.company c
            JOIN source.instance i ON i.id = c.instance_id
            WHERE c.instance_id = %s AND c.external_company_key = %s
            """,
            (instance_id, ADVANCLY_COMPANY_ID),
        ).fetchone()
        if row is None:
            raise SyncFailure("Advancly was matched but the company row is missing.")
        return row

    def _probe(self, conn, instance_id, matched, companies, message) -> None:
        conn.execute(
            """
            INSERT INTO source.company_probe
              (instance_id, matched, expected_company_id, companies, message)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (instance_id, matched, ADVANCLY_COMPANY_ID, Json(companies), redact(message, self.settings.secret_values())),
        )

    def _notice(self, conn, level: str, message: str) -> None:
        with conn.transaction():
            conn.execute(
                "INSERT INTO ops.operator_notice (level, message) VALUES (%s, %s)",
                (level, redact(message, self.settings.secret_values())),
            )


def fail_interrupted_runs(database_url: str) -> None:
    """A crashed process leaves a RUNNING row, which blocks the stream. The checkpoint stays put."""
    with connect(database_url) as conn:
        conn.execute(
            """
            UPDATE source.ingestion_run
            SET status = 'FAILED', finished_at = clock_timestamp(),
                error_summary = 'The process stopped before this run committed. The checkpoint was not moved.'
            WHERE status = 'RUNNING'
            """
        )
        conn.commit()
