"""Password sign-in against the appraisal project. The browser never receives those tokens."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
from fastapi import Request

from app.allowlist import normalise_email
from app.appraisal import (
    AuthRejected,
    AuthUnavailable,
    CanonicalEmailFailed,
    ProfileUnavailable,
)
from app.iam_config import BROWSER_ORIGINS, EXPECTED_ISS
from app.jwks import TokenRejected, verify_access_token
from app.session_store import (
    decide_grants,
    insert_session,
    key_bytes,
    provider_id,
    upsert_principal,
)
from app.temp_login import open_temp_session

log = logging.getLogger("vggos")

GENERIC = "Those credentials were not accepted."
UNAVAILABLE = "Sign-in could not be completed."
PASSWORD_CHANGE = "Finish the password change on the appraisal site before using VGG OS."
NO_GRANT = "Signed in, but this account has no finance grant."
CLASH = "This sign-in matches more than one allowlist entry. The grant was left pending."
NOT_CONFIGURED = "Sign-in is not configured."
RATE_LIMIT = "Too many sign-in attempts. Wait and try again."
CSRF_MESSAGE = "Reload the sign-in page and try again."
HTTPS_MESSAGE = "Sign-in is only accepted over HTTPS."


@dataclass
class LoginResult:
    status: int
    message: str = ""
    session_id: str = ""
    csrf: str = ""


def same(left: str, right: str) -> bool:
    left = left or ""
    right = right or ""
    if len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def request_is_local(request: Request) -> bool:
    host = (request.url.hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1", "testserver"}


def request_is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return forwarded == "https"


def cookie_is_secure(request: Request) -> bool:
    """Secure only when the browser is actually on HTTPS.

    Loopback HTTP stays non-Secure even if a proxy adds X-Forwarded-Proto.
    Chrome will not send a Secure cookie back to an http://localhost page,
    which made every sign-in attempt fail the form check.
    """
    if request.url.scheme != "https" and request_is_local(request):
        return False
    return request_is_https(request)


def issue_form_csrf(secret: str) -> str:
    """A signed form token. The browser does not have to echo a cookie."""
    expires = int(time.time()) + 30 * 60
    nonce = secrets.token_hex(16)
    payload = f"{expires}.{nonce}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def form_csrf_ok(token: str, secret: str) -> bool:
    if not secret or not token or token.count(".") != 2:
        return False
    expires_text, nonce, signature = token.split(".", 2)
    if not expires_text.isdigit() or len(nonce) != 32 or len(signature) != 64:
        return False
    payload = f"{expires_text}.{nonce}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not same(signature, expected):
        return False
    expires = int(expires_text)
    now = int(time.time())
    return now <= expires <= now + 31 * 60


def origin_allowed(request: Request, origin: str | None) -> bool:
    origin = (origin or "").split(",")[0].strip()
    if not origin:
        return True
    if origin in BROWSER_ORIGINS:
        return True
    if request_is_local(request):
        log.info("Sign-in origin %r is not listed; allowing because this host is local.", origin)
        return True
    log.warning("Sign-in form was rejected before the password check (origin=%r).", origin)
    return False


async def read_sign_in(request: Request) -> tuple[str, str, str]:
    """Read the form or JSON body and keep it off the log."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        email = str(payload.get("email") or "")
        password = str(payload.get("password") or "")
        csrf = str(payload.get("csrf_token") or request.headers.get("x-csrf-token") or "")
        return email, password, csrf
    form = await request.form()
    return str(form.get("email") or ""), str(form.get("password") or ""), str(form.get("csrf_token") or "")


def establish_session(request: Request, email: str, password: str, csrf_token: str, settings, appraisal, jwks, limiter, db) -> LoginResult:
    origin = request.headers.get("origin")
    if not origin_allowed(request, origin):
        return LoginResult(403, CSRF_MESSAGE)
    if not request_is_https(request) and not request_is_local(request):
        return LoginResult(400, HTTPS_MESSAGE)
    if not form_csrf_ok(csrf_token, settings.session_secret):
        log.warning("Sign-in form was rejected before the password check (form token).")
        return LoginResult(403, CSRF_MESSAGE)

    typed = email.strip()
    client_host = request.client.host if request.client else "unknown"
    if not limiter.allow(f"ip:{client_host}"):
        log.info("Sign-in rate limit.")
        return LoginResult(429, RATE_LIMIT)
    normalised = normalise_email(typed)
    if normalised and not limiter.allow(f"email:{normalised}"):
        log.info("Sign-in rate limit.")
        return LoginResult(429, RATE_LIMIT)
    if not typed or not password or len(password) > 1024:
        return LoginResult(401, GENERIC)

    if settings.temp_db_login:
        try:
            with db() as conn:
                temporary = open_temp_session(conn, settings, typed, password)
                if temporary is not None:
                    conn.commit()
                    return temporary
        except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege):
            log.info("Temporary database sign-in is not installed.")
        except Exception as exc:
            log.warning("Temporary database sign-in could not be checked (%s).", type(exc).__name__)
            return LoginResult(503, UNAVAILABLE)

    key = key_bytes(settings.token_key)
    if not settings.auth_anon_key or not settings.auth_url or key is None:
        return LoginResult(503, NOT_CONFIGURED)

    try:
        canonical = appraisal.canonical_email(typed)
    except CanonicalEmailFailed:
        log.info("Sign-in stopped because the login address could not be resolved.")
        return LoginResult(401, GENERIC)
    canonical_normal = normalise_email(canonical)
    if not canonical_normal:
        return LoginResult(401, GENERIC)
    if canonical_normal != normalised and not limiter.allow(f"email:{canonical_normal}"):
        log.info("Sign-in rate limit.")
        return LoginResult(429, RATE_LIMIT)

    try:
        access_token, refresh_token = appraisal.password_grant(canonical, password)
    except AuthRejected:
        log.info("Sign-in was rejected.")
        return LoginResult(401, GENERIC)
    except AuthUnavailable:
        log.info("Sign-in could not reach the appraisal project.")
        return LoginResult(503, UNAVAILABLE)
    finally:
        password = ""

    try:
        claims = verify_access_token(access_token, jwks)
    except TokenRejected as exc:
        log.info("Sign-in token was rejected (%s).", exc.code)
        return LoginResult(401, GENERIC)
    subject = claims["sub"]

    try:
        profile = appraisal.profile(subject, access_token)
    except ProfileUnavailable:
        log.info("Sign-in profile check failed.")
        return LoginResult(403, PASSWORD_CHANGE)
    if profile is None or profile.get("must_change_password") is not False:
        return LoginResult(403, PASSWORD_CHANGE)

    name = profile.get("name") if isinstance(profile.get("name"), str) else ""
    profile_email = normalise_email(profile.get("email") if isinstance(profile.get("email"), str) else "")
    display_email = profile_email or canonical_normal

    try:
        with db() as conn:
            principal = upsert_principal(conn, provider_id(conn), subject, name.strip(), display_email)
            emails = list(dict.fromkeys(item for item in (normalised, canonical_normal, profile_email) if item))
            decision = decide_grants(conn, subject, emails)
            if decision.kind == "clash":
                conn.commit()
                log.info("Allowlist clash for issuer %s. Grant left pending.", EXPECTED_ISS)
                return LoginResult(403, CLASH)
            if decision.kind != "grants":
                conn.commit()
                log.info("Sign-in has no finance grant.")
                return LoginResult(403, NO_GRANT)
            expires = datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
            session_id, csrf = insert_session(conn, key, principal, access_token, refresh_token, expires)
            conn.execute(
                """
                INSERT INTO audit.event (actor_id, action, object_type, object_id, outcome, details)
                VALUES (%s, 'SIGN_IN', 'session', %s, 'SUCCESS', '{}'::jsonb)
                """,
                (principal, session_id),
            )
            conn.commit()
    except Exception as exc:
        log.warning("Sign-in could not be stored (%s).", type(exc).__name__)
        return LoginResult(503, UNAVAILABLE)
    return LoginResult(200, "", session_id, csrf)
