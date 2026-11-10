"""Temporary database passwords. Appraisal sign-in stays the main login.

These accounts exist only so the app can be opened while that sign-in is being repaired.
Set VGGOS_TEMP_DB_LOGIN=0 to ignore them. Drop iam.temp_credential when they are no longer needed.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.allowlist import normalise_email
from app.iam_config import SCOPE_ACTION
from app.session_store import SESSION_HOURS, insert_session, key_bytes, upsert_principal

log = logging.getLogger("vggos")

TEMP_MARKER = "temp-db-login"
TEMP_PROVIDER = "TEMP_DB"
ALL_SCOPES = frozenset(SCOPE_ACTION)


def hash_password(password: str, salt: bytes | None = None) -> str:
    raw_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=raw_salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${raw_salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        return secrets.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def is_temp_token(token: str) -> bool:
    return token == TEMP_MARKER


def temp_session_still_allowed(conn, settings, subject: str) -> bool:
    """Checked on every request, so switching the login off or deactivating the row ends open sessions."""
    if not settings.temp_db_login:
        return False
    row = conn.execute(
        "SELECT active FROM iam.temp_credential WHERE external_subject = %s",
        (subject,),
    ).fetchone()
    return bool(row and row["active"])


def open_temp_session(conn, settings, email: str, password: str):
    """Return a LoginResult when this email is a temporary account, otherwise None."""
    from app.login_flow import GENERIC, LoginResult, UNAVAILABLE

    normalised = normalise_email(email)
    if not normalised:
        return None
    row = conn.execute(
        """
        SELECT email, display_name, password_hash, external_subject, active
        FROM iam.temp_credential
        WHERE email = %s
        """,
        (normalised,),
    ).fetchone()
    if row is None:
        return None
    if not row["active"] or not verify_password(password, row["password_hash"]):
        log.info("Temporary database sign-in was rejected.")
        return LoginResult(401, GENERIC)

    key = key_bytes(settings.token_key)
    if key is None:
        return LoginResult(503, UNAVAILABLE)
    provider = conn.execute(
        """
        SELECT id FROM iam.identity_provider
        WHERE code = %s AND configuration_status = 'CONFIRMED'
        """,
        (TEMP_PROVIDER,),
    ).fetchone()
    if provider is None:
        return LoginResult(503, UNAVAILABLE)

    principal = upsert_principal(conn, provider["id"], row["external_subject"], row["display_name"], row["email"])
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    session_id, csrf = insert_session(conn, key, principal, TEMP_MARKER, TEMP_MARKER, expires)
    conn.execute(
        """
        INSERT INTO audit.event (actor_id, action, object_type, object_id, outcome, details)
        VALUES (%s, 'SIGN_IN', 'session', %s, 'SUCCESS', '{"via":"temp_db"}'::jsonb)
        """,
        (principal, session_id),
    )
    log.info("Temporary database sign-in opened a session.")
    return LoginResult(200, "", session_id, csrf)


def _ensure_full_grants(conn, email: str, display_name: str, subject: str) -> None:
    conn.execute("DELETE FROM iam.finance_grant WHERE email = %s OR external_subject = %s", (email, subject))
    for scope in ALL_SCOPES:
        conn.execute(
            """
            INSERT INTO iam.finance_grant
              (email, display_name, scope, action, external_subject, status, bound_at)
            VALUES (%s, %s, %s, %s, %s, 'BOUND', clock_timestamp())
            """,
            (email, display_name, scope, SCOPE_ACTION[scope], subject),
        )


def seed_account(conn, email: str, display_name: str, subject: str, password: str, scopes: tuple[str, ...] | None = None) -> None:
    """Insert one temporary account with full access. Caller commits."""
    conn.execute(
        """
        INSERT INTO iam.temp_credential (email, display_name, password_hash, external_subject, active)
        VALUES (%s, %s, %s, %s, true)
        ON CONFLICT (email) DO UPDATE
        SET display_name = EXCLUDED.display_name,
            password_hash = EXCLUDED.password_hash,
            external_subject = EXCLUDED.external_subject,
            active = true
        """,
        (email, display_name, hash_password(password), subject),
    )
    _ensure_full_grants(conn, email, display_name, subject)
