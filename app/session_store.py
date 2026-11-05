"""OS sessions and finance grants. Appraisal tokens are encrypted before they are stored."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.iam_config import EXPECTED_ISS

SESSION_HOURS = 12
REFRESH_WITHIN = timedelta(seconds=120)


@dataclass
class SessionView:
    session_id: str
    principal_id: int
    subject: str
    display_name: str
    email: str
    scopes: frozenset[str]
    csrf: str


@dataclass
class GrantDecision:
    kind: str
    scopes: frozenset[str]


def key_bytes(value: str) -> bytes | None:
    text = (value or "").strip()
    if len(text) != 64:
        return None
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    if len(raw) != 32:
        return None
    return raw


def seal(key: bytes, session_id: str, plaintext: str) -> bytes:
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), session_id.encode("utf-8"))
    return nonce + ciphertext


def unseal(key: bytes, session_id: str, blob: bytes) -> str:
    raw = bytes(blob)
    nonce, ciphertext = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, session_id.encode("utf-8")).decode("utf-8")


def provider_id(conn) -> int:
    row = conn.execute(
        """
        SELECT id FROM iam.identity_provider
        WHERE issuer = %s AND configuration_status = 'CONFIRMED'
        """,
        (EXPECTED_ISS,),
    ).fetchone()
    if row is None:
        raise LookupError("Appraisal identity provider is not installed.")
    return row["id"]


def upsert_principal(conn, provider: int, subject: str, name: str, email: str) -> int:
    row = conn.execute(
        """
        INSERT INTO iam.principal (provider_id, external_subject, display_name, email, principal_type)
        VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''), 'USER')
        ON CONFLICT (provider_id, external_subject) DO UPDATE
        SET display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), iam.principal.display_name),
            email = COALESCE(NULLIF(EXCLUDED.email, ''), iam.principal.email)
        RETURNING id
        """,
        (provider, subject, name, email),
    ).fetchone()
    return row["id"]


def decide_grants(conn, subject: str, emails: list[str]) -> GrantDecision:
    """Bind a pending allowlist email once. After that, only the subject matters.

    A grant row is never inserted here. Two allowlist identities for one sign-in stay pending.
    """
    bound = conn.execute(
        """
        SELECT scope FROM iam.finance_grant
        WHERE external_subject = %s AND status = 'BOUND'
        """,
        (subject,),
    ).fetchall()
    if bound:
        return GrantDecision("grants", frozenset(row["scope"] for row in bound))

    if not emails:
        return GrantDecision("none", frozenset())

    rows = conn.execute(
        """
        SELECT id, email, display_name, external_subject, scope, status
        FROM iam.finance_grant
        WHERE email = ANY(%s)
        FOR UPDATE
        """,
        (emails,),
    ).fetchall()
    if not rows:
        return GrantDecision("none", frozenset())

    by_email: dict[str, list] = {}
    for row in rows:
        by_email.setdefault(row["email"], []).append(row)

    if len(by_email) > 1:
        _record_clash(conn, subject, emails, "This sign-in matched more than one allowlist email.")
        return GrantDecision("clash", frozenset())

    email, group = next(iter(by_email.items()))
    names = {(row["display_name"] or "").strip().lower() for row in group if (row["display_name"] or "").strip()}
    if len(names) > 1:
        _record_clash(conn, subject, [email], "This email is on the allowlist for more than one account.")
        return GrantDecision("clash", frozenset())

    taken = {row["external_subject"] for row in group if row["external_subject"]}
    if taken and taken != {subject}:
        _record_clash(conn, subject, [email], "This email is already bound to a different account.")
        return GrantDecision("clash", frozenset())

    conn.execute(
        """
        UPDATE iam.finance_grant
        SET external_subject = %s, status = 'BOUND', bound_at = clock_timestamp(), clash_detail = NULL
        WHERE email = %s AND status = 'PENDING' AND external_subject IS NULL
        """,
        (subject, email),
    )
    granted = conn.execute(
        """
        SELECT scope FROM iam.finance_grant
        WHERE external_subject = %s AND status = 'BOUND'
        """,
        (subject,),
    ).fetchall()
    if not granted:
        return GrantDecision("none", frozenset())
    return GrantDecision("grants", frozenset(row["scope"] for row in granted))


def _record_clash(conn, subject: str, emails: list[str], detail: str) -> None:
    for email in emails:
        conn.execute(
            """
            INSERT INTO iam.grant_clash (email, attempted_subject, detail)
            VALUES (%s, %s, %s)
            """,
            (email, subject, detail),
        )
        conn.execute(
            """
            UPDATE iam.finance_grant
            SET clash_detail = %s
            WHERE email = %s AND status = 'PENDING'
            """,
            (detail, email),
        )


def insert_session(conn, key: bytes, principal_id: int, access_token: str, refresh_token: str, expires_at: datetime) -> tuple[str, str]:
    session_id = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO iam.os_session (
          id, principal_id, refresh_ciphertext, access_ciphertext, access_expires_at, csrf_token
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            session_id,
            principal_id,
            seal(key, session_id, refresh_token),
            seal(key, session_id, access_token),
            expires_at,
            csrf,
        ),
    )
    return session_id, csrf


def delete_session(conn, session_id: str) -> None:
    if session_id:
        conn.execute("DELETE FROM iam.os_session WHERE id = %s", (session_id,))


def load_session_row(conn, session_id: str):
    if not session_id:
        return None
    return conn.execute(
        """
        SELECT s.id, s.principal_id, s.refresh_ciphertext, s.access_ciphertext, s.access_expires_at,
               s.csrf_token, s.created_at, p.external_subject, p.display_name, p.email, p.active
        FROM iam.os_session s
        JOIN iam.principal p ON p.id = s.principal_id
        WHERE s.id = %s
        """,
        (session_id,),
    ).fetchone()


def scopes_for(conn, subject: str) -> frozenset[str]:
    rows = conn.execute(
        """
        SELECT scope FROM iam.finance_grant
        WHERE external_subject = %s AND status = 'BOUND'
        """,
        (subject,),
    ).fetchall()
    return frozenset(row["scope"] for row in rows)


def session_view(row, scopes: frozenset[str]) -> SessionView:
    return SessionView(
        session_id=row["id"],
        principal_id=row["principal_id"],
        subject=row["external_subject"],
        display_name=row["display_name"] or "",
        email=row["email"] or "",
        scopes=scopes,
        csrf=row["csrf_token"],
    )


def session_expired(row, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    created = row["created_at"]
    if created is None:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created + timedelta(hours=SESSION_HOURS) <= current


def access_needs_refresh(row, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    expires = row["access_expires_at"]
    if expires is None:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= current + REFRESH_WITHIN


def replace_tokens(conn, key: bytes, session_id: str, access_token: str, refresh_token: str, expires_at: datetime) -> None:
    conn.execute(
        """
        UPDATE iam.os_session
        SET refresh_ciphertext = %s, access_ciphertext = %s, access_expires_at = %s
        WHERE id = %s
        """,
        (seal(key, session_id, refresh_token), seal(key, session_id, access_token), expires_at, session_id),
    )
