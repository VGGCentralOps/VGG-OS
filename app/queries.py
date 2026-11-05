"""Read queries for the screens. The default register does not select bank account secrets or raw payloads."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from app.constants import ADVANCLY_ENTITY_CODE
from app.money import money_text
from app.trial_balance import merge_trial_balance

REGISTER_SQL = """
SELECT e.name AS entity_name,
       a.account_display,
       a.currency_code,
       a.bank_name,
       a.active,
       g.bc_account_id,
       g.accountable_owner,
       g.data_route,
       g.link_status,
       g.in_latest_bc_list
FROM finance.bank_account a
JOIN core.entity e ON e.id = a.entity_id
JOIN finance.bank_account_governance g ON g.bank_account_id = a.id
WHERE e.code = %s
ORDER BY a.bank_name, g.bc_account_id
"""

UNMASKED_REGISTER_SQL = """
SELECT e.name AS entity_name,
       COALESCE(sec.bank_account_number, a.account_display) AS account_display,
       a.currency_code,
       a.bank_name,
       a.active,
       g.bc_account_id,
       g.accountable_owner,
       g.data_route,
       g.link_status,
       g.in_latest_bc_list
FROM finance.bank_account a
JOIN core.entity e ON e.id = a.entity_id
JOIN finance.bank_account_governance g ON g.bank_account_id = a.id
LEFT JOIN finance.bank_account_secret sec ON sec.bank_account_id = a.id
WHERE e.code = %s
ORDER BY a.bank_name, g.bc_account_id
"""

RUN_SQL = """
SELECT r.id, r.status, r.started_at, r.finished_at, r.rows_read, r.rows_accepted, r.rows_rejected,
       r.error_summary, r.checkpoint_before, r.checkpoint_after, s.endpoint_code, s.committed_checkpoint
FROM source.ingestion_run r
LEFT JOIN source.sync_stream s ON s.id = r.stream_id
LEFT JOIN source.company c ON c.id = s.company_id
LEFT JOIN core.entity e ON e.id = c.entity_id
WHERE e.code = %s
ORDER BY r.id DESC
LIMIT 80
"""


def register_rows(conn, unmask: bool = False) -> list[dict]:
    statement = UNMASKED_REGISTER_SQL if unmask else REGISTER_SQL
    return conn.execute(statement, (ADVANCLY_ENTITY_CODE,)).fetchall()


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
    return conn.execute(RUN_SQL, (ADVANCLY_ENTITY_CODE,)).fetchall()


def latest_probe(conn) -> dict | None:
    return conn.execute(
        """
        SELECT probed_at, matched, expected_company_id, companies, message
        FROM source.company_probe
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


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


def advancly_bank_count(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM finance.bank_account a
        JOIN core.entity e ON e.id = a.entity_id
        WHERE e.code = %s
        """,
        (ADVANCLY_ENTITY_CODE,),
    ).fetchone()
    return int(row["n"])


def open_task_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM ops.bank_mismatch_task WHERE status = 'OPEN'"
    ).fetchone()
    return int(row["n"])


def loaded_months(conn) -> list[tuple[int, int]]:
    rows = conn.execute(
        """
        SELECT y, m FROM (
          SELECT EXTRACT(YEAR FROM e.posting_date)::int AS y, EXTRACT(MONTH FROM e.posting_date)::int AS m
          FROM finance.gl_entry e
          JOIN core.entity n ON n.id = e.entity_id
          WHERE n.code = %s AND NOT e.source_deleted
          UNION
          SELECT b.posting_year, b.posting_month
          FROM finance.bc_gl_period_balance b
          JOIN source.company c ON c.id = b.company_id
          JOIN core.entity n ON n.id = c.entity_id
          WHERE n.code = %s
        ) months
        ORDER BY y DESC, m DESC
        """,
        (ADVANCLY_ENTITY_CODE, ADVANCLY_ENTITY_CODE),
    ).fetchall()
    return [(row["y"], row["m"]) for row in rows]


def trial_balance(conn, year: int, month: int) -> dict:
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
        (ADVANCLY_ENTITY_CODE, start, end),
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
        (ADVANCLY_ENTITY_CODE, year, month),
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
        (ADVANCLY_ENTITY_CODE, start, end),
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
        (ADVANCLY_ENTITY_CODE,),
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
