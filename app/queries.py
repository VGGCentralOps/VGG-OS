"""Read queries for the screens. The default register does not select bank account secrets or raw payloads."""

from __future__ import annotations

import time
from calendar import monthrange
from datetime import date
from decimal import Decimal

from app.constants import ADVANCLY_ENTITY_CODE
from app.money import money_text
from app.trial_balance import merge_trial_balance

REGISTER_SQL = """
SELECT a.id AS bank_account_id,
       e.name AS entity_name,
       a.account_display,
       a.currency_code,
       a.bank_name,
       a.active,
       g.bc_account_id,
       g.accountable_owner,
       g.data_route,
       g.link_status,
       g.in_latest_bc_list,
       CASE
         WHEN NULLIF(BTRIM(m.payload->>'balance'), '') IS NULL THEN NULL
         ELSE (m.payload->>'balance')::numeric
       END AS book_balance,
       CASE
         WHEN NULLIF(BTRIM(m.payload->>'balanceLCY'), '') IS NULL THEN NULL
         ELSE (m.payload->>'balanceLCY')::numeric
       END AS book_balance_lcy
FROM finance.bank_account a
JOIN core.entity e ON e.id = a.entity_id
JOIN finance.bank_account_governance g ON g.bank_account_id = a.id
LEFT JOIN finance.bank_book_mapping map ON map.bank_account_id = a.id
LEFT JOIN finance.bc_master_record m
  ON m.company_id = map.company_id
 AND m.entity_set = 'bankAccounts'
 AND NOT m.source_deleted
 AND m.record_id = g.bc_account_id
WHERE e.code = %s
ORDER BY a.bank_name, g.bc_account_id
"""

UNMASKED_REGISTER_SQL = """
SELECT a.id AS bank_account_id,
       e.name AS entity_name,
       COALESCE(sec.bank_account_number, a.account_display) AS account_display,
       a.currency_code,
       a.bank_name,
       a.active,
       g.bc_account_id,
       g.accountable_owner,
       g.data_route,
       g.link_status,
       g.in_latest_bc_list,
       CASE
         WHEN NULLIF(BTRIM(m.payload->>'balance'), '') IS NULL THEN NULL
         ELSE (m.payload->>'balance')::numeric
       END AS book_balance,
       CASE
         WHEN NULLIF(BTRIM(m.payload->>'balanceLCY'), '') IS NULL THEN NULL
         ELSE (m.payload->>'balanceLCY')::numeric
       END AS book_balance_lcy
FROM finance.bank_account a
JOIN core.entity e ON e.id = a.entity_id
JOIN finance.bank_account_governance g ON g.bank_account_id = a.id
LEFT JOIN finance.bank_account_secret sec ON sec.bank_account_id = a.id
LEFT JOIN finance.bank_book_mapping map ON map.bank_account_id = a.id
LEFT JOIN finance.bc_master_record m
  ON m.company_id = map.company_id
 AND m.entity_set = 'bankAccounts'
 AND NOT m.source_deleted
 AND m.record_id = g.bc_account_id
WHERE e.code = %s
ORDER BY a.bank_name, g.bc_account_id
"""

RUN_SQL = """
SELECT r.id, r.status, r.started_at, r.finished_at, r.rows_read, r.rows_accepted, r.rows_rejected,
       r.error_summary, r.checkpoint_before, r.checkpoint_after, s.endpoint_code, s.committed_checkpoint,
       e.name AS company_name
FROM source.ingestion_run r
LEFT JOIN source.sync_stream s ON s.id = r.stream_id
LEFT JOIN source.company c ON c.id = s.company_id
LEFT JOIN core.entity e ON e.id = c.entity_id
ORDER BY r.id DESC
LIMIT 80
"""


def _has_bank_ledger(conn) -> bool:
    row = conn.execute("SELECT to_regclass('finance.bank_ledger_entry') IS NOT NULL AS ok").fetchone()
    return bool(row and row["ok"])


def register_rows(conn, entity_code: str = ADVANCLY_ENTITY_CODE, unmask: bool = False) -> list[dict]:
    statement = UNMASKED_REGISTER_SQL if unmask else REGISTER_SQL
    rows = conn.execute(statement, (entity_code,)).fetchall()
    activity = {}
    if _has_bank_ledger(conn):
        activity = {
            row["bank_account_id"]: row
            for row in conn.execute(
                """
                SELECT bank_account_id, COUNT(*) AS entries, MAX(posting_date) AS last_posting_date
                FROM finance.bank_ledger_entry
                WHERE bank_account_id IS NOT NULL AND NOT source_deleted
                GROUP BY bank_account_id
                """
            ).fetchall()
        }
    for row in rows:
        found = activity.get(row["bank_account_id"])
        row["entries"] = int(found["entries"]) if found else 0
        row["last_posting_date"] = found["last_posting_date"] if found else None
        balance = row.get("book_balance")
        if balance is not None and not isinstance(balance, Decimal):
            balance = Decimal(str(balance))
            row["book_balance"] = balance
        row["book_balance_text"] = money_text(balance)
        balance_lcy = row.get("book_balance_lcy")
        if balance_lcy is not None and not isinstance(balance_lcy, Decimal):
            balance_lcy = Decimal(str(balance_lcy))
            row["book_balance_lcy"] = balance_lcy
        row["book_balance_lcy_text"] = money_text(balance_lcy)
    return rows


MOVEMENT_LIMIT = 500


def movement_accounts(conn, entity_code: str = ADVANCLY_ENTITY_CODE) -> list[dict]:
    return conn.execute(
        """
        SELECT a.id, a.bank_name, a.account_display, a.currency_code
        FROM finance.bank_account a
        JOIN core.entity e ON e.id = a.entity_id
        WHERE e.code = %s
        ORDER BY a.bank_name, a.id
        """,
        (entity_code,),
    ).fetchall()


def movement_months(conn, entity_code: str = ADVANCLY_ENTITY_CODE) -> list[tuple[int, int]]:
    if not _has_bank_ledger(conn):
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT EXTRACT(YEAR FROM l.posting_date)::int AS y, EXTRACT(MONTH FROM l.posting_date)::int AS m
        FROM finance.bank_ledger_entry l
        JOIN core.entity e ON e.id = l.entity_id
        WHERE e.code = %s AND NOT l.source_deleted
        ORDER BY y DESC, m DESC
        """,
        (entity_code,),
    ).fetchall()
    return [(row["y"], row["m"]) for row in rows]


def _movement_filter(
    entity_code: str,
    account_id: int | None,
    year: int | None,
    month: int | None,
) -> tuple[str, list]:
    clauses = ["e.code = %s", "NOT l.source_deleted"]
    params: list = [entity_code]
    if account_id is not None:
        clauses.append("l.bank_account_id = %s")
        params.append(account_id)
    if year and month:
        start = date(year, month, 1)
        clauses.append("l.posting_date BETWEEN %s AND %s")
        params.extend([start, date(year, month, monthrange(year, month)[1])])
    return " AND ".join(clauses), params


def bank_movements(
    conn,
    entity_code: str,
    account_id: int | None,
    year: int | None,
    month: int | None,
    limit: int | None = MOVEMENT_LIMIT,
) -> dict:
    if not _has_bank_ledger(conn):
        return {"rows": [], "summary": [], "total_rows": 0, "truncated": False}
    where, params = _movement_filter(entity_code, account_id, year, month)
    total = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM finance.bank_ledger_entry l
        JOIN core.entity e ON e.id = l.entity_id
        WHERE {where}
        """,
        params,
    ).fetchone()["n"]
    limit_sql = "" if limit is None else f"LIMIT {int(limit)}"
    rows = conn.execute(
        f"""
        SELECT l.posting_date, l.document_type, l.document_number, l.external_document_number,
               l.description, l.currency_code, l.amount, l.amount_lcy, l.open, l.statement_status,
               l.reversed, l.bc_bank_account_number, a.bank_name, a.account_display
        FROM finance.bank_ledger_entry l
        JOIN core.entity e ON e.id = l.entity_id
        LEFT JOIN finance.bank_account a ON a.id = l.bank_account_id
        WHERE {where}
        ORDER BY l.posting_date DESC, l.entry_number DESC NULLS LAST, l.id DESC
        {limit_sql}
        """,
        params,
    ).fetchall()
    for row in rows:
        amount = row["amount"]
        row["direction"] = None if amount is None else ("in" if amount > 0 else "out" if amount < 0 else None)
        row["amount_text"] = money_text(amount)
        row["amount_lcy_text"] = money_text(row["amount_lcy"])
        if not row["bank_name"]:
            row["bank_name"] = "Not in register"
            row["account_display"] = "Unlinked"
    summary = conn.execute(
        f"""
        SELECT l.bank_account_id, COALESCE(a.bank_name, 'Not in register') AS bank_name,
               COALESCE(a.account_display, 'Unlinked') AS account_display,
               COALESCE(a.currency_code, MIN(l.currency_code)) AS currency_code,
               COUNT(*) AS entries,
               SUM(l.amount) FILTER (WHERE l.amount > 0) AS inflow,
               SUM(l.amount) FILTER (WHERE l.amount < 0) AS outflow,
               SUM(l.amount) AS net,
               COUNT(*) FILTER (WHERE l.amount IS NULL) AS missing,
               MIN(l.posting_date) AS first_date, MAX(l.posting_date) AS last_date
        FROM finance.bank_ledger_entry l
        JOIN core.entity e ON e.id = l.entity_id
        LEFT JOIN finance.bank_account a ON a.id = l.bank_account_id
        WHERE {where}
        GROUP BY l.bank_account_id, a.bank_name, a.account_display, a.currency_code
        ORDER BY bank_name
        """,
        params,
    ).fetchall()
    for item in summary:
        complete = not item["missing"]
        # With every amount present, "no inflow rows" is a real zero. With any amount missing,
        # a total would understate the movement, so it is shown as missing instead.
        item["inflow_text"] = money_text(item["inflow"] if item["inflow"] is not None or not complete else Decimal(0))
        item["outflow_text"] = money_text(item["outflow"] if item["outflow"] is not None or not complete else Decimal(0))
        item["net_text"] = money_text(item["net"]) if complete else money_text(None)
    return {"rows": rows, "summary": summary, "total_rows": int(total), "truncated": limit is not None and total > len(rows)}


def movement_count(conn, entity_code: str = ADVANCLY_ENTITY_CODE) -> int:
    if not _has_bank_ledger(conn):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM finance.bank_ledger_entry l
        JOIN core.entity e ON e.id = l.entity_id
        WHERE e.code = %s AND NOT l.source_deleted
        """,
        (entity_code,),
    ).fetchone()
    return int(row["n"])


def run_count(conn) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM source.ingestion_run").fetchone()
    return int(row["n"])


def last_run(conn) -> dict | None:
    return conn.execute(
        """
        SELECT r.status, s.endpoint_code, r.started_at, r.finished_at, r.rows_accepted, r.rows_rejected,
               r.error_summary
        FROM source.ingestion_run r
        JOIN source.sync_stream s ON s.id = r.stream_id
        ORDER BY r.id DESC
        LIMIT 1
        """
    ).fetchone()


def open_tasks(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT mismatch_code, assignee_role, bank_account_id, feed_reference, detail, opened_at
        FROM ops.bank_mismatch_task
        WHERE status = 'OPEN'
        ORDER BY mismatch_code, id
        """
    ).fetchall()


def sync_runs(conn) -> list[dict]:
    return conn.execute(RUN_SQL).fetchall()


def latest_probe(conn) -> dict | None:
    has_entity_sets = conn.execute(
        """
        SELECT EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'source' AND table_name = 'company_probe' AND column_name = 'entity_sets'
        ) AS ok
        """
    ).fetchone()["ok"]
    columns = "probed_at, matched, expected_company_id, companies, message"
    if has_entity_sets:
        columns += ", entity_sets"
    row = conn.execute(
        f"""
        SELECT {columns}
        FROM source.company_probe
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is not None and "entity_sets" not in row:
        row = dict(row)
        row["entity_sets"] = None
    return row


def notices(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT created_at, level, message
        FROM ops.operator_notice
        ORDER BY id DESC
        LIMIT 8
        """
    ).fetchall()


def pending_report_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM finance.report WHERE catalogue_status = 'PENDING'"
    ).fetchone()
    return int(row["n"])


def report_catalogue(conn) -> list[dict]:
    return conn.execute(
        """
        SELECT code, name, catalogue_status, cadence
        FROM finance.report
        ORDER BY name
        """
    ).fetchall()


def company_coverage(conn) -> list[dict]:
    """Per-company ledger coverage in one round trip. Used by Overview and the shell."""
    return conn.execute(
        """
        SELECT e.id AS entity_id,
               e.code AS entity_code,
               e.name AS entity_name,
               c.id AS company_id,
               COALESCE(a.accounts, 0) AS accounts,
               COALESCE(b.bank_accounts, 0) AS bank_accounts,
               COALESCE(g.gl_entries, 0) AS gl_entries,
               g.gl_from,
               g.gl_to
        FROM core.entity e
        JOIN source.company c ON c.entity_id = e.id
        LEFT JOIN LATERAL (
          SELECT COUNT(*)::int AS accounts
          FROM finance.account x
          WHERE x.entity_id = e.id AND x.active IS DISTINCT FROM false
        ) a ON true
        LEFT JOIN LATERAL (
          SELECT COUNT(*)::int AS bank_accounts
          FROM finance.bank_account x
          WHERE x.entity_id = e.id
        ) b ON true
        LEFT JOIN LATERAL (
          SELECT COUNT(*)::int AS gl_entries,
                 MIN(x.posting_date) AS gl_from,
                 MAX(x.posting_date) AS gl_to
          FROM finance.gl_entry x
          WHERE x.entity_id = e.id AND NOT x.source_deleted
        ) g ON true
        ORDER BY e.name
        """
    ).fetchall()


_SHELL_ENTITIES_CACHE: tuple[float, list[dict]] | None = None
_SHELL_ENTITIES_TTL = 30.0


def shell_entities(conn, *, refresh: bool = False) -> list[dict]:
    """Light company list for the shell. Cached briefly — remote DB RTT dominates."""
    global _SHELL_ENTITIES_CACHE
    now = time.monotonic()
    if not refresh and _SHELL_ENTITIES_CACHE and now - _SHELL_ENTITIES_CACHE[0] < _SHELL_ENTITIES_TTL:
        return [dict(row) for row in _SHELL_ENTITIES_CACHE[1]]
    rows = conn.execute(
        """
        SELECT e.code,
               e.name,
               EXISTS (
                 SELECT 1 FROM finance.bank_account a
                 WHERE a.entity_id = e.id
               ) AS has_banks,
               EXISTS (
                 SELECT 1 FROM finance.bc_gl_period_balance b
                 JOIN source.company c2 ON c2.id = b.company_id
                 WHERE c2.entity_id = e.id
               ) AS has_gl
        FROM core.entity e
        JOIN source.company c ON c.entity_id = e.id
        ORDER BY e.name
        """
    ).fetchall()
    payload = [dict(row) for row in rows]
    _SHELL_ENTITIES_CACHE = (now, payload)
    return [dict(row) for row in payload]


def shell_period(conn, entity_code: str = ADVANCLY_ENTITY_CODE) -> tuple[int, int] | None:
    row = conn.execute(
        """
        SELECT posting_year AS y, posting_month AS m
        FROM finance.bc_gl_period_balance b
        JOIN source.company c ON c.id = b.company_id
        JOIN core.entity n ON n.id = c.entity_id
        WHERE n.code = %s
        ORDER BY posting_year DESC, posting_month DESC
        LIMIT 1
        """,
        (entity_code,),
    ).fetchone()
    if row:
        return int(row["y"]), int(row["m"])
    row = conn.execute(
        """
        SELECT EXTRACT(YEAR FROM g.posting_date)::int AS y,
               EXTRACT(MONTH FROM g.posting_date)::int AS m
        FROM finance.gl_entry g
        JOIN core.entity n ON n.id = g.entity_id
        WHERE n.code = %s AND NOT g.source_deleted
        ORDER BY g.posting_date DESC
        LIMIT 1
        """,
        (entity_code,),
    ).fetchone()
    return (int(row["y"]), int(row["m"])) if row else None


def shell_context(conn, entity_code: str | None = None) -> dict:
    """Entities + period for the selected company in one round trip."""
    entities = shell_entities(conn)
    codes = {row["code"] for row in entities}
    code = entity_code if entity_code in codes else (ADVANCLY_ENTITY_CODE if ADVANCLY_ENTITY_CODE in codes else (entities[0]["code"] if entities else ADVANCLY_ENTITY_CODE))
    period = None
    if entities:
        # Period lookup is folded into the entities query when possible via a second statement
        # on the same connection; keep it cheap (indexed period balance first).
        period = shell_period(conn, code)
    active = next((row for row in entities if row["code"] == code), None)
    return {"shell_entities": entities, "active_entity": active, "entity_code": code, "shell_period": period}


def entity_bank_count(conn, entity_code: str = ADVANCLY_ENTITY_CODE) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM finance.bank_account a
        JOIN core.entity e ON e.id = a.entity_id
        WHERE e.code = %s
        """,
        (entity_code,),
    ).fetchone()
    return int(row["n"])


def advancly_bank_count(conn) -> int:
    return entity_bank_count(conn, ADVANCLY_ENTITY_CODE)


def open_task_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ops.bank_mismatch_task WHERE status = 'OPEN'"
    ).fetchone()
    return int(row["n"])


TASK_TITLES = {
    "IN_BC_NO_FEED": "In register, no feed",
    "FEED_WITHOUT_BC": "Feed without register",
    "LINKED_STALE": "Linked but stale",
}


def recent_library(conn, limit: int = 8) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, name, kind, size_bytes, created_at
        FROM finance.repository_node
        ORDER BY created_at DESC, sort_index, name
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    for row in rows:
        size = row["size_bytes"]
        if row["kind"] != "file" or size is None:
            row["size_label"] = None
        elif size < 1024:
            row["size_label"] = f"{size} B"
        elif size < 1024 * 1024:
            row["size_label"] = f"{size / 1024:.1f} KB"
        else:
            row["size_label"] = f"{size / (1024 * 1024):.1f} MB"
    return rows


def active_bank_accounts(conn, entity_code: str = ADVANCLY_ENTITY_CODE, limit: int = 6) -> list[dict]:
    if not _has_bank_ledger(conn):
        return []
    return conn.execute(
        """
        SELECT a.id, a.bank_name, a.account_display, a.currency_code,
               COUNT(*) AS entries, MAX(l.posting_date) AS last_posting_date
        FROM finance.bank_ledger_entry l
        JOIN finance.bank_account a ON a.id = l.bank_account_id
        JOIN core.entity e ON e.id = a.entity_id
        WHERE e.code = %s AND NOT l.source_deleted
        GROUP BY a.id, a.bank_name, a.account_display, a.currency_code
        ORDER BY entries DESC, last_posting_date DESC NULLS LAST
        LIMIT %s
        """,
        (entity_code, limit),
    ).fetchall()


def recent_gl_lines(conn, entity_code: str = ADVANCLY_ENTITY_CODE, limit: int = 8) -> list[dict]:
    rows = conn.execute(
        """
        SELECT e.name AS company_name, g.posting_date, g.document_reference, g.amount, a.account_code, a.account_name
        FROM finance.gl_entry g
        JOIN core.entity e ON e.id = g.entity_id
        LEFT JOIN finance.account a ON a.id = g.account_id
        WHERE e.code = %s AND NOT g.source_deleted
        ORDER BY g.posting_date DESC, g.id DESC
        LIMIT %s
        """,
        (entity_code, limit),
    ).fetchall()
    for row in rows:
        row["amount_text"] = money_text(row["amount"])
    return rows


def overview_board(conn, entity_code: str = ADVANCLY_ENTITY_CODE, entities: list[dict] | None = None) -> dict:
    """Overview payload with few remote round trips (DB is often far from the app)."""
    companies = company_coverage(conn)
    selected = next((row for row in companies if row.get("entity_code") == entity_code), None)
    if entities is None:
        entities = [
            {
                "code": row["entity_code"],
                "name": row["entity_name"],
                "has_gl": int(row["gl_entries"] or 0) > 0,
                "has_banks": int(row["bank_accounts"] or 0) > 0,
            }
            for row in companies
        ]
    board = conn.execute(
        """
        WITH selected AS (
          SELECT e.id AS entity_id
          FROM core.entity e
          WHERE e.code = %s
        ),
        kpi AS (
          SELECT
            (SELECT COUNT(*) FROM finance.report) AS report_count,
            (SELECT COUNT(*) FROM finance.report WHERE catalogue_status = 'PUBLISHED') AS published_report_count,
            (SELECT COUNT(*) FROM finance.report WHERE catalogue_status = 'PENDING') AS pending_report_count,
            (SELECT COUNT(*) FROM finance.repository_node WHERE kind = 'file') AS file_count,
            (SELECT COUNT(*) FROM ops.bank_mismatch_task WHERE status = 'OPEN') AS task_count,
            (
              SELECT COUNT(*) FROM finance.bank_ledger_entry l
              JOIN selected s ON s.entity_id = l.entity_id
              WHERE NOT l.source_deleted
            ) AS movement_count
        ),
        period AS (
          SELECT b.posting_year AS y, b.posting_month AS m
          FROM finance.bc_gl_period_balance b
          JOIN source.company c ON c.id = b.company_id
          JOIN selected s ON s.entity_id = c.entity_id
          ORDER BY b.posting_year DESC, b.posting_month DESC
          LIMIT 1
        ),
        months AS (
          SELECT b.posting_year AS y, b.posting_month AS m
          FROM finance.bc_gl_period_balance b
          JOIN source.company c ON c.id = b.company_id
          JOIN selected s ON s.entity_id = c.entity_id
          ORDER BY b.posting_year DESC, b.posting_month DESC
          LIMIT 6
        ),
        recent_gl AS (
          SELECT g.posting_date, g.document_reference, g.amount, a.account_code, a.account_name,
                 e.name AS company_name
          FROM finance.gl_entry g
          JOIN selected s ON s.entity_id = g.entity_id
          JOIN core.entity e ON e.id = g.entity_id
          LEFT JOIN finance.account a ON a.id = g.account_id
          WHERE NOT g.source_deleted
          ORDER BY g.posting_date DESC, g.id DESC
          LIMIT 8
        ),
        active_banks AS (
          SELECT a.id, a.bank_name, a.account_display, a.currency_code,
                 COUNT(*) AS entries, MAX(l.posting_date) AS last_posting_date
          FROM finance.bank_ledger_entry l
          JOIN selected s ON s.entity_id = l.entity_id
          JOIN finance.bank_account a ON a.id = l.bank_account_id
          WHERE NOT l.source_deleted
          GROUP BY a.id, a.bank_name, a.account_display, a.currency_code
          ORDER BY entries DESC, last_posting_date DESC NULLS LAST
          LIMIT 6
        ),
        tasks AS (
          SELECT mismatch_code, assignee_role, bank_account_id, feed_reference, detail, opened_at
          FROM ops.bank_mismatch_task
          WHERE status = 'OPEN'
          ORDER BY mismatch_code, id
          LIMIT 8
        ),
        library AS (
          SELECT id, name, kind, size_bytes, created_at
          FROM finance.repository_node
          ORDER BY created_at DESC, sort_index, name
          LIMIT 8
        )
        SELECT
          (SELECT row_to_json(kpi) FROM kpi) AS kpi,
          (SELECT row_to_json(period) FROM period) AS period,
          COALESCE((SELECT json_agg(months) FROM months), '[]'::json) AS trial_months,
          COALESCE((SELECT json_agg(recent_gl) FROM recent_gl), '[]'::json) AS recent_gl,
          COALESCE((SELECT json_agg(active_banks) FROM active_banks), '[]'::json) AS active_banks,
          COALESCE((SELECT json_agg(tasks) FROM tasks), '[]'::json) AS tasks,
          COALESCE((SELECT json_agg(library) FROM library), '[]'::json) AS library
        """,
        (entity_code,),
    ).fetchone()
    kpi = board["kpi"] or {}
    period_row = board["period"]
    period = (int(period_row["y"]), int(period_row["m"])) if period_row else None
    if period is None and selected and selected.get("gl_to"):
        stamp = selected["gl_to"]
        period = (stamp.year, stamp.month)
    trial_months = [(int(row["y"]), int(row["m"])) for row in (board["trial_months"] or [])]
    recent_gl = list(board["recent_gl"] or [])
    for row in recent_gl:
        amount = row.get("amount")
        if amount is not None and not isinstance(amount, Decimal):
            amount = Decimal(str(amount))
            row["amount"] = amount
        row["amount_text"] = money_text(amount)
    library = list(board["library"] or [])
    for row in library:
        size = row.get("size_bytes")
        if row.get("kind") != "file" or size is None:
            row["size_label"] = None
        elif size < 1024:
            row["size_label"] = f"{size} B"
        elif size < 1024 * 1024:
            row["size_label"] = f"{size / 1024:.1f} KB"
        else:
            row["size_label"] = f"{size / (1024 * 1024):.1f} MB"
    tasks = []
    for task in board["tasks"] or []:
        item = dict(task)
        item["title"] = TASK_TITLES.get(item.get("mismatch_code"), item.get("mismatch_code") or "Open item")
        tasks.append(item)
    return {
        "totals": {"companies": len(companies)},
        "companies": companies[:6],
        "selected_company": selected,
        "tasks": tasks,
        "task_count": int(kpi.get("task_count") or 0),
        "bank_count": int((selected or {}).get("bank_accounts") or 0),
        "movement_count": int(kpi.get("movement_count") or 0),
        "account_count": int((selected or {}).get("accounts") or 0),
        "gl_count": int((selected or {}).get("gl_entries") or 0),
        "report_count": int(kpi.get("report_count") or 0),
        "published_report_count": int(kpi.get("published_report_count") or 0),
        "pending_report_count": int(kpi.get("pending_report_count") or 0),
        "file_count": int(kpi.get("file_count") or 0),
        "library": library,
        "active_banks": list(board["active_banks"] or []),
        "recent_gl": recent_gl,
        "trial_months": trial_months,
        "movement_months": trial_months,
        "shell_entities": entities,
        "shell_period": period,
    }


def loaded_months(conn, entity_code: str = ADVANCLY_ENTITY_CODE) -> list[tuple[int, int]]:
    rows = conn.execute(
        """
        SELECT b.posting_year AS y, b.posting_month AS m
        FROM finance.bc_gl_period_balance b
        JOIN source.company c ON c.id = b.company_id
        JOIN core.entity n ON n.id = c.entity_id
        WHERE n.code = %s
        ORDER BY y DESC, m DESC
        """,
        (entity_code,),
    ).fetchall()
    if rows:
        return [(int(row["y"]), int(row["m"])) for row in rows]
    rows = conn.execute(
        """
        SELECT DISTINCT EXTRACT(YEAR FROM e.posting_date)::int AS y,
               EXTRACT(MONTH FROM e.posting_date)::int AS m
        FROM finance.gl_entry e
        JOIN core.entity n ON n.id = e.entity_id
        WHERE n.code = %s AND NOT e.source_deleted
        ORDER BY y DESC, m DESC
        """,
        (entity_code,),
    ).fetchall()
    return [(int(row["y"]), int(row["m"])) for row in rows]


def trial_balance(conn, year: int, month: int, entity_code: str = ADVANCLY_ENTITY_CODE) -> dict:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    local = {}
    for row in conn.execute(
        """
        SELECT a.account_code, a.account_name, a.account_type,
               SUM(e.amount) AS amount, SUM(e.debit_amount) AS debit, SUM(e.credit_amount) AS credit,
               COUNT(*) AS count
        FROM finance.gl_entry e
        JOIN finance.account a ON a.id = e.account_id
        JOIN core.entity n ON n.id = e.entity_id
        WHERE n.code = %s AND e.posting_date BETWEEN %s AND %s AND NOT e.source_deleted
        GROUP BY a.account_code, a.account_name, a.account_type
        """,
        (entity_code, start, end),
    ).fetchall():
        local[row["account_code"]] = {
            "name": row["account_name"],
            "account_type": row["account_type"],
            "amount": row["amount"],
            "debit": row["debit"],
            "credit": row["credit"],
            "count": row["count"],
        }
    control = {}
    for row in conn.execute(
        """
        SELECT b.gl_account_number, b.amount, b.debit_amount, b.credit_amount, b.entry_count
        FROM finance.bc_gl_period_balance b
        JOIN source.company c ON c.id = b.company_id
        JOIN core.entity n ON n.id = c.entity_id
        WHERE n.code = %s AND b.posting_year = %s AND b.posting_month = %s
        """,
        (entity_code, year, month),
    ).fetchall():
        control[row["gl_account_number"]] = {
            "amount": row["amount"],
            "debit": row["debit_amount"],
            "credit": row["credit_amount"],
            "entry_count": row["entry_count"],
        }
    rows = []
    for item in merge_trial_balance(local, control):
        item["local_text"] = money_text(item["local_amount"])
        item["bc_text"] = money_text(item["bc_amount"])
        item["difference_text"] = "Not comparable" if item["difference"] is None else money_text(item["difference"])
        rows.append(item)
    unbalanced = conn.execute(
        """
        SELECT e.transaction_number, SUM(e.amount) AS total, COUNT(*) AS lines
        FROM finance.gl_entry e
        JOIN core.entity n ON n.id = e.entity_id
        WHERE n.code = %s AND e.posting_date BETWEEN %s AND %s AND NOT e.source_deleted
        GROUP BY e.transaction_number
        HAVING SUM(e.amount) <> 0
        ORDER BY e.transaction_number
        """,
        (entity_code, start, end),
    ).fetchall()
    problems = conn.execute(
        """
        SELECT v.rule_code, v.message, v.checked_at
        FROM source.validation_result v
        JOIN source.ingestion_run r ON r.id = v.run_id
        JOIN source.sync_stream s ON s.id = r.stream_id
        JOIN source.company c ON c.id = s.company_id
        JOIN core.entity e ON e.id = c.entity_id
        WHERE e.code = %s AND v.passed = false
          AND v.rule_code IN ('UNMAPPED_OR_REJECTED', 'TB_CONTROL_DIFFERENCE', 'TB_SIDE_MISSING', 'TRANSACTION_NOT_BALANCED')
        ORDER BY v.id DESC
        LIMIT 200
        """,
        (entity_code,),
    ).fetchall()
    local_sum = None
    if local:
        local_sum = sum(row["amount"] for row in local.values())
    return {
        "rows": rows,
        "unbalanced": unbalanced,
        "problems": problems,
        "local_sum": local_sum,
        "local_sum_text": money_text(local_sum) if local else None,
        "has_local": bool(local),
        "has_control": bool(control),
    }


def dormancy_rule(conn) -> dict | None:
    return conn.execute(
        """
        SELECT applies_to, inactive_after_months, dormant_after_months, enabled, note
        FROM finance.bank_dormancy_rule
        WHERE applies_to = 'GROUP_DEFAULT'
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()


BOOKS_SAMPLE = 25


def books_inventory(conn) -> dict:
    """Counts and sample rows for every dataset the books pull can populate.

    Empty sets stay present so each stream has a dedicated section even before rows land.
    """
    companies = conn.execute(
        """
        SELECT c.id AS company_id,
               c.external_company_key,
               e.id AS entity_id,
               e.code AS entity_code,
               e.name AS entity_name
        FROM source.company c
        JOIN core.entity e ON e.id = c.entity_id
        ORDER BY e.name
        """
    ).fetchall()
    company_counts = conn.execute(
        """
        SELECT c.id AS company_id,
               (SELECT COUNT(*) FROM finance.account a WHERE a.entity_id = c.entity_id AND a.active IS DISTINCT FROM false) AS accounts,
               (SELECT COUNT(*) FROM finance.bank_account b WHERE b.entity_id = c.entity_id) AS bank_accounts,
               (SELECT COUNT(*) FROM finance.gl_entry g WHERE g.entity_id = c.entity_id AND NOT g.source_deleted) AS gl_entries,
               (SELECT COUNT(*) FROM finance.bank_ledger_entry l WHERE l.entity_id = c.entity_id AND NOT l.source_deleted) AS bank_ledger_entries,
               (SELECT COUNT(*) FROM finance.bc_master_record m WHERE m.company_id = c.id AND NOT m.source_deleted) AS master_records,
               (SELECT MIN(g.posting_date) FROM finance.gl_entry g WHERE g.entity_id = c.entity_id AND NOT g.source_deleted) AS gl_from,
               (SELECT MAX(g.posting_date) FROM finance.gl_entry g WHERE g.entity_id = c.entity_id AND NOT g.source_deleted) AS gl_to,
               (SELECT MIN(l.posting_date) FROM finance.bank_ledger_entry l WHERE l.entity_id = c.entity_id AND NOT l.source_deleted) AS bank_from,
               (SELECT MAX(l.posting_date) FROM finance.bank_ledger_entry l WHERE l.entity_id = c.entity_id AND NOT l.source_deleted) AS bank_to
        FROM source.company c
        """
    ).fetchall()
    by_company = {row["company_id"]: row for row in company_counts}
    company_rows = []
    for company in companies:
        counts = by_company.get(company["company_id"], {})
        company_rows.append({**company, **counts})

    master_by_set = conn.execute(
        """
        SELECT entity_set, COUNT(*) AS n
        FROM finance.bc_master_record
        WHERE NOT source_deleted
        GROUP BY entity_set
        ORDER BY entity_set
        """
    ).fetchall()
    expected_sets = [
        "generalLedgerSetups",
        "accountingPeriods",
        "glAccounts",
        "dimensions",
        "dimensionValues",
        "dimensionSetEntries",
        "bankAccounts",
        "glEntries",
        "bankAccountLedgerEntries",
        "glBalancesByPeriod",
    ]
    master_map = {row["entity_set"]: int(row["n"]) for row in master_by_set}
    dataset_counts = [{"entity_set": name, "n": master_map.get(name, 0)} for name in expected_sets]
    for row in master_by_set:
        if row["entity_set"] not in expected_sets:
            dataset_counts.append({"entity_set": row["entity_set"], "n": int(row["n"])})

    accounts = conn.execute(
        """
        SELECT e.name AS company_name, a.account_code, a.account_name, a.account_type, a.active
        FROM finance.account a
        JOIN core.entity e ON e.id = a.entity_id
        ORDER BY e.name, a.account_code
        LIMIT %s
        """,
        (BOOKS_SAMPLE,),
    ).fetchall()
    bank_accounts = conn.execute(
        """
        SELECT e.name AS company_name, a.bank_name, a.account_display, a.currency_code, a.active, g.bc_account_id
        FROM finance.bank_account a
        JOIN core.entity e ON e.id = a.entity_id
        LEFT JOIN finance.bank_account_governance g ON g.bank_account_id = a.id
        ORDER BY e.name, a.bank_name, g.bc_account_id
        LIMIT %s
        """,
        (BOOKS_SAMPLE,),
    ).fetchall()
    gl_entries = conn.execute(
        """
        SELECT e.name AS company_name, g.posting_date, g.document_reference, g.amount, a.account_code, a.account_name
        FROM finance.gl_entry g
        JOIN core.entity e ON e.id = g.entity_id
        LEFT JOIN finance.account a ON a.id = g.account_id
        WHERE NOT g.source_deleted
        ORDER BY g.posting_date DESC, g.id DESC
        LIMIT %s
        """,
        (BOOKS_SAMPLE,),
    ).fetchall()
    for row in gl_entries:
        row["amount_text"] = money_text(row["amount"])
    bank_ledger = []
    if _has_bank_ledger(conn):
        bank_ledger = conn.execute(
            """
            SELECT e.name AS company_name, l.posting_date, l.document_number, l.description,
                   l.amount, l.currency_code, a.account_display, a.bank_name
            FROM finance.bank_ledger_entry l
            JOIN core.entity e ON e.id = l.entity_id
            LEFT JOIN finance.bank_account a ON a.id = l.bank_account_id
            WHERE NOT l.source_deleted
            ORDER BY l.posting_date DESC, l.id DESC
            LIMIT %s
            """,
            (BOOKS_SAMPLE,),
        ).fetchall()
        for row in bank_ledger:
            row["amount_text"] = money_text(row["amount"])
    master_samples = conn.execute(
        """
        SELECT e.name AS company_name, m.entity_set, m.record_id, m.business_key
        FROM finance.bc_master_record m
        JOIN source.company c ON c.id = m.company_id
        JOIN core.entity e ON e.id = c.entity_id
        WHERE NOT m.source_deleted
        ORDER BY m.entity_set, e.name, m.record_id
        LIMIT %s
        """,
        (BOOKS_SAMPLE,),
    ).fetchall()
    totals = {
        "companies": len(company_rows),
        "accounts": sum(int(row.get("accounts") or 0) for row in company_rows),
        "bank_accounts": sum(int(row.get("bank_accounts") or 0) for row in company_rows),
        "gl_entries": sum(int(row.get("gl_entries") or 0) for row in company_rows),
        "bank_ledger_entries": sum(int(row.get("bank_ledger_entries") or 0) for row in company_rows),
        "master_records": sum(int(row.get("master_records") or 0) for row in company_rows),
    }
    return {
        "companies": company_rows,
        "dataset_counts": dataset_counts,
        "accounts": accounts,
        "bank_accounts": bank_accounts,
        "gl_entries": gl_entries,
        "bank_ledger": bank_ledger,
        "master_samples": master_samples,
        "totals": totals,
        "sample_limit": BOOKS_SAMPLE,
    }
