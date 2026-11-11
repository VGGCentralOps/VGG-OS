"""VGG OS web process. The browser does not receive Business Central credentials or appraisal tokens."""

from __future__ import annotations

import csv
import io
import logging
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from psycopg.types.json import Json

from app.appraisal import default_client
from app.constants import ADVANCLY_DIMENSION_CODES, ADVANCLY_WORKBOOK_NAME, SQL_PACK_VERSION
from app.db import app_connection
from app.feeds import FeedClosed, MonoClient, StatementFileDrop
from app.iam_config import CSRF_COOKIE, SESSION_COOKIE, AuthKeyRefused, require_anon_key
from app.jwks import JwksCache
from app.login_flow import cookie_is_secure, establish_session, issue_form_csrf, read_sign_in, same
from app.queries import (
    advancly_bank_count,
    bank_movements,
    dormancy_rule,
    last_run,
    latest_probe,
    loaded_months,
    movement_accounts,
    movement_count,
    movement_months,
    open_task_count,
    open_tasks,
    register_rows,
    books_inventory,
    sync_runs,
    trial_balance,
)
from app.rate_limit import RateLimiter
from app.repository import (
    file_count,
    MAX_BYTES,
    add_file,
    add_folder,
    breadcrumbs,
    children,
    clean_name,
    content_disposition,
    delete_node,
    extension_mime,
    file_payload,
    folder_node,
    message_for,
    parse_node_id,
    size_label,
)
from app.redact import ScrubFilter
from app.session_request import end_session, resolve_session
from app.session_store import SessionView
from app.settings import Settings, load_settings
from app.sync_service import SyncService, fail_interrupted_runs

log = logging.getLogger("vggos")
BASE = __import__("pathlib").Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE / "templates"))
_SYNC = {"running": False}
_SYNC_LOCK = threading.Lock()


def install_scrub() -> None:
    for name in ("", "vggos", "httpx", "httpcore", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, ScrubFilter) for item in logger.filters):
            logger.addFilter(ScrubFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def health_body(database: str, business_central: str) -> dict:
    return {
        "service": "vgg-os",
        "database": database,
        "business_central": business_central,
        "sql_pack": SQL_PACK_VERSION,
    }


def set_session_cookie(response, session_id: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=60 * 60 * 12,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response, *, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")


def clear_stale_csrf_cookie(response) -> None:
    """Drop the old double-submit cookie, including one stored with Secure."""
    for secure in (False, True):
        response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=True, samesite="lax")


def wants_json(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    accept = request.headers.get("accept", "")
    return "application/json" in content_type or "application/json" in accept


def optional_int(value: str | int | None) -> int | None:
    """HTML forms send empty strings for blank fields; treat those as missing."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def create_app(settings: Settings | None = None, appraisal=None, jwks=None, limiter=None) -> FastAPI:
    current = settings or load_settings()
    require_anon_key(current.auth_anon_key)
    install_scrub()
    app = FastAPI(title="VGG OS", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.middleware("http")
    async def cache_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    app.state.settings = current
    app.state.appraisal = appraisal or default_client(current.auth_url, current.auth_anon_key)
    app.state.jwks = jwks or JwksCache()
    app.state.limiter = limiter or RateLimiter()
    if current.database_url:
        try:
            fail_interrupted_runs(current.database_url)
        except Exception:
            log.warning("Could not check for an interrupted sync.")

    def db():
        return app_connection(current)

    def resolved(request: Request):
        return resolve_session(
            request.cookies.get(SESSION_COOKIE, ""),
            current,
            app.state.appraisal,
            app.state.jwks,
            db,
        )

    def login_page(request: Request, error: str | None, status_code: int = 200):
        token = issue_form_csrf(current.session_secret)
        response = TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"request": request, "csrf": token, "error": error},
            status_code=status_code,
        )
        clear_stale_csrf_cookie(response)
        response.headers["Cache-Control"] = "no-store"
        # Chrome sends Origin: null on a form post when this is no-referrer,
        # and sign-in rejects that before it reads the password.
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def page(request: Request, session: SessionView, name: str, **context):
        scopes = session.scopes
        context.update(
            {
                "request": request,
                "csrf": session.csrf,
                "scopes": sorted(scopes),
                "can_export": "export" in scopes,
                "can_sync": "books:sync" in scopes,
                "can_write": "files:write" in scopes,
                "unmasked": "bank:unmask" in scopes,
                "principal_name": session.display_name,
                "sql_pack": SQL_PACK_VERSION,
                "bc_state": current.bc_state(),
            }
        )
        response = TEMPLATES.TemplateResponse(request, name, context)
        response.headers["Cache-Control"] = "no-store"
        return response

    def signed_in(request: Request):
        found = resolved(request)
        if found.session is None:
            response = RedirectResponse("/login", status_code=303)
            if found.clear_cookie:
                clear_session_cookie(response, secure=cookie_is_secure(request))
            response.headers["Cache-Control"] = "no-store"
            return response
        return found.session

    def guard(request: Request, scope: str):
        session = signed_in(request)
        if not isinstance(session, SessionView):
            return session
        if scope not in session.scopes:
            response = page(request, session, "forbidden.html")
            response.status_code = 403
            return response
        return session

    def guard_api(request: Request, scope: str):
        found = resolved(request)
        if found.session is None:
            response = JSONResponse({"status": "sign-in required"}, status_code=401)
            if found.clear_cookie:
                clear_session_cookie(response, secure=cookie_is_secure(request))
            return response
        if scope not in found.session.scopes:
            return JSONResponse({"status": "not permitted"}, status_code=403)
        return found.session

    @app.get("/health")
    def health():
        database = "down"
        try:
            with db() as conn:
                conn.execute("SELECT 1")
            database = "up"
        except Exception:
            database = "down"
        body = health_body(database, current.bc_state())
        return JSONResponse(body, status_code=200 if database == "up" else 503)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        found = resolved(request)
        if found.session is not None:
            return RedirectResponse("/", status_code=303)
        response = login_page(request, None)
        if found.clear_cookie:
            clear_session_cookie(response, secure=cookie_is_secure(request))
        return response

    @app.post("/api/session")
    async def open_session(request: Request):
        email, password, csrf_token = await read_sign_in(request)
        try:
            result = establish_session(
                request,
                email,
                password,
                csrf_token,
                current,
                app.state.appraisal,
                app.state.jwks,
                app.state.limiter,
                db,
            )
        finally:
            password = ""
            email = ""
        if result.status == 200 and result.session_id:
            if wants_json(request):
                response = JSONResponse({"ok": True})
            else:
                response = RedirectResponse("/", status_code=303)
            set_session_cookie(response, result.session_id, secure=cookie_is_secure(request))
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        if wants_json(request):
            response = JSONResponse({"ok": False, "message": result.message}, status_code=result.status)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        return login_page(request, result.message, result.status)

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form("")):
        found = resolved(request)
        if found.session is not None and not same(csrf_token, found.session.csrf):
            return RedirectResponse("/", status_code=303)
        end_session(request.cookies.get(SESSION_COOKIE, ""), current, app.state.appraisal, db)
        response = RedirectResponse("/login", status_code=303)
        clear_session_cookie(response, secure=cookie_is_secure(request))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        with db() as conn:
            inventory = books_inventory(conn)
            totals = inventory.get("totals") or {}
            payload = {
                "company_count": totals.get("companies", 0),
                "account_count": totals.get("accounts", 0),
                "bank_count": advancly_bank_count(conn),
                "task_count": open_task_count(conn),
                "file_count": file_count(conn),
                "movement_count": movement_count(conn),
            }
        return page(request, session, "overview.html", **payload)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        session = signed_in(request)
        if not isinstance(session, SessionView):
            return session
        return page(request, session, "settings.html")

    @app.get("/books", response_class=HTMLResponse)
    def books(request: Request):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        with db() as conn:
            runs = sync_runs(conn)
            probe = latest_probe(conn)
            inventory = books_inventory(conn)
            entity_sets = None
            if probe and isinstance(probe.get("entity_sets"), dict):
                entity_sets = probe["entity_sets"].get("matches")
            payload = {
                "runs": runs,
                "probe": probe,
                "entity_sets": entity_sets,
                "blockers": current.connection_blockers(),
                "expected_id": current.company_key(),
                "expected_name": ADVANCLY_WORKBOOK_NAME,
                "dimensions": ADVANCLY_DIMENSION_CODES,
                "sync_running": _SYNC["running"],
                "secret_reference": current.bc_secret_reference,
                **inventory,
            }
        return page(request, session, "books.html", **payload)

    @app.get("/reports")
    def reports(request: Request):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        return RedirectResponse("/repositories", status_code=303)

    def repository_location(parent, error: str = "") -> str:
        base = "/repositories" if parent is None else f"/repositories/{parent}"
        if error:
            return f"{base}?error={error}"
        return base

    def repository_screen(request: Request, session: SessionView, node_id: str | None):
        with db() as conn:
            folder = None
            if node_id is not None:
                parsed = parse_node_id(node_id)
                folder = folder_node(conn, parsed) if parsed is not None else None
                if folder is None:
                    return RedirectResponse("/repositories", status_code=303)
            parent = None if folder is None else folder["id"]
            crumbs = [] if parent is None else breadcrumbs(conn, parent)
            nodes = [
                {**node, "size_label": size_label(node["size_bytes"])}
                for node in children(conn, parent)
            ]
        return page(
            request,
            session,
            "repositories.html",
            folder=folder,
            crumbs=crumbs,
            nodes=nodes,
            notice=message_for(request.query_params.get("error")),
        )

    @app.get("/repositories", response_class=HTMLResponse)
    def repositories(request: Request):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        return repository_screen(request, session, None)

    @app.get("/repositories/{node_id}", response_class=HTMLResponse)
    def repository_folder(request: Request, node_id: str):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        return repository_screen(request, session, node_id)

    @app.get("/repositories/{node_id}/file")
    def repository_file(request: Request, node_id: str):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        parsed = parse_node_id(node_id)
        if parsed is None:
            return Response("Not found", status_code=404)
        with db() as conn:
            payload = file_payload(conn, parsed)
        if payload is None:
            return Response("Not found", status_code=404)
        return Response(
            bytes(payload["content"]),
            media_type=payload["mime"] or "application/octet-stream",
            headers={
                "Content-Disposition": content_disposition(payload["name"]),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/repositories/upload")
    async def repository_upload(
        request: Request,
        csrf_token: str = Form(""),
        parent_id: str = Form(""),
        upload: UploadFile | None = File(None),
    ):
        session = guard(request, "files:write")
        if not isinstance(session, SessionView):
            return session
        parent = None
        if parent_id.strip():
            parent = parse_node_id(parent_id)
            if parent is None:
                return RedirectResponse("/repositories?error=folder", status_code=303)
        if not same(csrf_token, session.csrf):
            return RedirectResponse(repository_location(parent), status_code=303)
        filename = upload.filename if upload is not None else ""
        name = clean_name(Path(filename or "").name)
        if name is None:
            return RedirectResponse(repository_location(parent, "name"), status_code=303)
        if extension_mime(name) is None:
            return RedirectResponse(repository_location(parent, "type"), status_code=303)
        content = b""
        total = 0
        too_large = False
        if upload is not None:
            while True:
                block = await upload.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_BYTES:
                    too_large = True
                    break
                content += block
        if too_large:
            return RedirectResponse(repository_location(parent, "size"), status_code=303)
        if not content:
            return RedirectResponse(repository_location(parent, "empty"), status_code=303)
        with db() as conn:
            code = add_file(conn, parent, name, content, session.principal_id)
        return RedirectResponse(repository_location(parent, code), status_code=303)

    @app.post("/repositories/folder")
    def repository_new_folder(
        request: Request,
        csrf_token: str = Form(""),
        parent_id: str = Form(""),
        name: str = Form(""),
    ):
        session = guard(request, "files:write")
        if not isinstance(session, SessionView):
            return session
        parent = None
        if parent_id.strip():
            parent = parse_node_id(parent_id)
            if parent is None:
                return RedirectResponse("/repositories?error=folder", status_code=303)
        if not same(csrf_token, session.csrf):
            return RedirectResponse(repository_location(parent), status_code=303)
        with db() as conn:
            code = add_folder(conn, parent, name, session.principal_id)
        return RedirectResponse(repository_location(parent, code), status_code=303)

    @app.post("/repositories/{node_id}/delete")
    def repository_delete(request: Request, node_id: str, csrf_token: str = Form("")):
        session = guard(request, "files:write")
        if not isinstance(session, SessionView):
            return session
        parsed = parse_node_id(node_id)
        if parsed is None or not same(csrf_token, session.csrf):
            return RedirectResponse("/repositories", status_code=303)
        with db() as conn:
            code, parent = delete_node(conn, parsed, session.principal_id)
        if code:
            return RedirectResponse("/repositories?error=folder", status_code=303)
        return RedirectResponse(repository_location(parent), status_code=303)

    @app.post("/sync")
    def start_sync(
        request: Request,
        csrf_token: str = Form(""),
        date_from: str = Form(""),
        date_to: str = Form(""),
        year: str = Form(""),
        month: str = Form(""),
        scope: str = Form("all"),
    ):
        session = guard(request, "books:sync")
        if not isinstance(session, SessionView):
            return session
        if not same(csrf_token, session.csrf):
            return RedirectResponse("/books", status_code=303)
        if current.connection_blockers():
            return RedirectResponse("/books", status_code=303)
        try:
            parsed_from = date.fromisoformat(date_from)
            parsed_to = date.fromisoformat(date_to)
            parsed_year = int(year)
            parsed_month = int(month)
            if not 1 <= parsed_month <= 12:
                raise ValueError
        except ValueError:
            return RedirectResponse("/books", status_code=303)
        all_companies = scope.strip().lower() != "one"
        principal_id = session.principal_id
        with _SYNC_LOCK:
            if _SYNC["running"]:
                return RedirectResponse("/books", status_code=303)
            _SYNC["running"] = True

        def work():
            try:
                service = SyncService(current)
                if all_companies:
                    service.run_all_companies(
                        parsed_from, parsed_to, parsed_year, parsed_month, actor_id=principal_id
                    )
                else:
                    service.run_advancly(
                        parsed_from, parsed_to, parsed_year, parsed_month, actor_id=principal_id
                    )
            finally:
                with _SYNC_LOCK:
                    _SYNC["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return RedirectResponse("/books", status_code=303)

    @app.post("/bank-accounts/check")
    def refresh_check(request: Request, csrf_token: str = Form("")):
        session = guard(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        if not same(csrf_token, session.csrf):
            return RedirectResponse("/bank-accounts", status_code=303)
        with db() as conn:
            row = conn.execute("SELECT id FROM core.entity WHERE code = 'ADVANCLY'").fetchone()
            if row:
                with conn.transaction():
                    SyncService(current).refresh_tasks(conn, row["id"])
            conn.commit()
        return RedirectResponse("/bank-accounts", status_code=303)

    @app.get("/trial-balance", response_class=HTMLResponse)
    def trial(request: Request, year: int | None = None, month: int | None = None):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        with db() as conn:
            months = loaded_months(conn)
            report = None
            if year and month and 1 <= month <= 12:
                report = trial_balance(conn, year, month)
        return page(
            request,
            session,
            "trial_balance.html",
            months=months,
            report=report,
            year=year,
            month=month,
        )

    @app.get("/trial-balance.csv")
    def trial_csv(request: Request, year: int, month: int):
        session = guard(request, "books:read")
        if not isinstance(session, SessionView):
            return session
        if "export" not in session.scopes:
            return JSONResponse({"status": "not permitted"}, status_code=403)
        with db() as conn:
            report = trial_balance(conn, year, month)
            _export(conn, session.principal_id, "trial-balance", {"year": year, "month": month}, len(report["rows"]))
            conn.commit()
        return Response(_trial_csv(report), media_type="text/csv", headers={"Cache-Control": "no-store"})

    @app.get("/bank-accounts", response_class=HTMLResponse)
    def banks(request: Request):
        session = guard(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        unmask = "bank:unmask" in session.scopes
        with db() as conn:
            rows = register_rows(conn, unmask=unmask)
            tasks = open_tasks(conn)
            rule = dormancy_rule(conn)
        grouped = {
            "IN_BC_NO_FEED": [task for task in tasks if task["mismatch_code"] == "IN_BC_NO_FEED"],
            "FEED_WITHOUT_BC": [task for task in tasks if task["mismatch_code"] == "FEED_WITHOUT_BC"],
            "LINKED_STALE": [task for task in tasks if task["mismatch_code"] == "LINKED_STALE"],
        }
        return page(
            request,
            session,
            "bank_accounts.html",
            rows=rows,
            grouped=grouped,
            rule=rule,
        )

    @app.get("/bank-movements", response_class=HTMLResponse)
    def movements(
        request: Request,
        account_id: str | None = None,
        year: str | None = None,
        month: str | None = None,
    ):
        session = guard(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        parsed_account = optional_int(account_id)
        parsed_year = optional_int(year)
        parsed_month = optional_int(month)
        chosen_account = parsed_account if parsed_account and parsed_account > 0 else None
        chosen_year = parsed_year if parsed_year and parsed_year >= 2000 else None
        chosen_month = parsed_month if parsed_month and 1 <= parsed_month <= 12 else None
        if (chosen_year is None) != (chosen_month is None):
            chosen_year = None
            chosen_month = None
        with db() as conn:
            accounts = movement_accounts(conn)
            months = movement_months(conn)
            report = bank_movements(conn, chosen_account, chosen_year, chosen_month)
        return page(
            request,
            session,
            "bank_movements.html",
            accounts=accounts,
            months=months,
            report=report,
            account_id=chosen_account,
            year=chosen_year,
            month=chosen_month,
        )

    @app.get("/bank-movements.csv")
    def movements_csv(
        request: Request,
        account_id: str | None = None,
        year: str | None = None,
        month: str | None = None,
    ):
        session = guard(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        if "export" not in session.scopes:
            return JSONResponse({"status": "not permitted"}, status_code=403)
        parsed_account = optional_int(account_id)
        parsed_year = optional_int(year)
        parsed_month = optional_int(month)
        chosen_account = parsed_account if parsed_account and parsed_account > 0 else None
        chosen_year = parsed_year if parsed_year and parsed_year >= 2000 else None
        chosen_month = parsed_month if parsed_month and 1 <= parsed_month <= 12 else None
        if (chosen_year is None) != (chosen_month is None):
            chosen_year = None
            chosen_month = None
        with db() as conn:
            report = bank_movements(conn, chosen_account, chosen_year, chosen_month, limit=None)
            _export(
                conn,
                session.principal_id,
                "bank-movements",
                {"account_id": chosen_account, "year": chosen_year, "month": chosen_month},
                report["total_rows"],
            )
            conn.commit()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "posting_date",
                "bank_name",
                "account_display",
                "document_type",
                "document_number",
                "description",
                "currency",
                "amount",
                "amount_lcy",
                "open",
                "statement_status",
                "reversed",
            ]
        )
        for row in report["rows"]:
            writer.writerow(
                [
                    row["posting_date"],
                    row["bank_name"],
                    row["account_display"],
                    row["document_type"] or "",
                    row["document_number"] or "",
                    row["description"] or "",
                    row["currency_code"] or "",
                    "" if row["amount"] is None else format(row["amount"], "f"),
                    "" if row["amount_lcy"] is None else format(row["amount_lcy"], "f"),
                    "" if row["open"] is None else ("yes" if row["open"] else "no"),
                    row["statement_status"] or "",
                    "" if row["reversed"] is None else ("yes" if row["reversed"] else "no"),
                ]
            )
        return Response(buffer.getvalue(), media_type="text/csv", headers={"Cache-Control": "no-store"})

    @app.get("/bank-accounts.csv")
    def banks_csv(request: Request):
        session = guard(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        if "export" not in session.scopes:
            return JSONResponse({"status": "not permitted"}, status_code=403)
        unmask = "bank:unmask" in session.scopes
        with db() as conn:
            rows = register_rows(conn, unmask=unmask)
            _export(conn, session.principal_id, "bank-register", {"entity": "ADVANCLY", "unmasked": unmask}, len(rows))
            conn.commit()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        number_column = "account_number" if unmask else "masked_account_number"
        writer.writerow(
            ["owning_entity", number_column, "currency", "name", "business_central_account_id",
             "accountable_owner", "data_route", "link_status", "in_latest_business_central_list", "blocked"]
        )
        for row in rows:
            writer.writerow([
                row["entity_name"], row["account_display"], row["currency_code"], row["bank_name"],
                row["bc_account_id"], row["accountable_owner"] or "", row["data_route"], row["link_status"],
                "yes" if row["in_latest_bc_list"] else "no", "yes" if not row["active"] else "no",
            ])
        return Response(buffer.getvalue(), media_type="text/csv", headers={"Cache-Control": "no-store"})

    @app.post("/bank-feeds/mono/pull")
    def mono_pull(request: Request):
        session = guard_api(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        try:
            MonoClient().pull_balances("not-used")
        except FeedClosed as exc:
            return JSONResponse({"status": "closed", "message": str(exc)}, status_code=423)
        return JSONResponse({"status": "closed"}, status_code=423)

    @app.post("/bank-feeds/statement-file")
    async def statement_drop(request: Request):
        session = guard_api(request, "bank:read")
        if not isinstance(session, SessionView):
            return session
        try:
            StatementFileDrop().accept("not-supplied", b"")
        except FeedClosed as exc:
            return JSONResponse({"status": "closed", "message": str(exc)}, status_code=423)
        return JSONResponse({"status": "closed"}, status_code=423)

    return app


def _export(conn, actor_id: int, kind: str, filters: dict, row_count: int) -> None:
    conn.execute(
        """
        INSERT INTO audit.export_event (actor_id, format, filters, row_count, outcome)
        VALUES (%s, 'CSV', %s, %s, 'SUCCESS')
        """,
        (actor_id, Json({"kind": kind, **filters}), row_count),
    )


def _trial_csv(report: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["account", "name", "local_net", "business_central_net", "difference"])
    for row in report["rows"]:
        writer.writerow([
            row["code"],
            row["name"] or "",
            "" if row["local_amount"] is None else format(row["local_amount"], "f"),
            "" if row["bc_amount"] is None else format(row["bc_amount"], "f"),
            "" if row["difference"] is None else format(row["difference"], "f"),
        ])
    return buffer.getvalue()


def _fallback() -> FastAPI:
    fallback = FastAPI(title="VGG OS", docs_url=None, redoc_url=None)

    @fallback.get("/health")
    def health():
        return JSONResponse(health_body("down", "not_configured"), status_code=503)

    return fallback


try:
    app = create_app()
except AuthKeyRefused:
    raise
except RuntimeError:
    app = _fallback()
