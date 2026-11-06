"""Resolve the OS cookie, refresh the appraisal session, and drop it when refresh fails."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app.jwks import verify_access_token
from app.session_store import (
    SessionView,
    access_needs_refresh,
    delete_session,
    key_bytes,
    load_session_row,
    replace_tokens,
    scopes_for,
    session_expired,
    session_view,
    unseal,
)
from app.temp_login import ALL_SCOPES, is_temp_token

log = logging.getLogger("vggos")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_SESSION_CACHE: dict[str, tuple[float, "Resolved"]] = {}
_CACHE_SECONDS = 30.0


def _cached(cookie: str) -> "Resolved | None":
    item = _SESSION_CACHE.get(cookie)
    if item is None or item[0] <= time.monotonic():
        _SESSION_CACHE.pop(cookie, None)
        return None
    return item[1]


def _remember(cookie: str, found: "Resolved") -> None:
    if found.session is not None:
        _SESSION_CACHE[cookie] = (time.monotonic() + _CACHE_SECONDS, found)


def forget_session(cookie: str) -> None:
    _SESSION_CACHE.pop(cookie or "", None)


@dataclass
class Resolved:
    session: SessionView | None = None
    clear_cookie: bool = False


def _temporary_session(key: bytes, row) -> bool:
    try:
        return is_temp_token(unseal(key, row["id"], row["access_ciphertext"]))
    except Exception:
        return False


def _lock_for(session_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[session_id] = lock
        return lock


def _drop(conn, session_id: str, message: str) -> Resolved:
    delete_session(conn, session_id)
    conn.commit()
    log.info(message)
    return Resolved(clear_cookie=True)


def resolve_session(cookie: str, settings, appraisal, jwks, db) -> Resolved:
    if not cookie:
        return Resolved()
    remembered = _cached(cookie)
    if remembered is not None:
        return remembered
    key = key_bytes(settings.token_key)
    if key is None:
        return Resolved(clear_cookie=True)
    with _lock_for(cookie):
        remembered = _cached(cookie)
        if remembered is not None:
            return remembered
        try:
            with db() as conn:
                row = load_session_row(conn, cookie)
                if row is None or not row["active"] or session_expired(row):
                    forget_session(cookie)
                    return _drop(conn, cookie, "OS session ended.")
                if _temporary_session(key, row):
                    view = session_view(row, ALL_SCOPES)
                    conn.commit()
                    found = Resolved(session=view)
                    _remember(cookie, found)
                    return found
                if access_needs_refresh(row):
                    try:
                        refresh_token = unseal(key, row["id"], row["refresh_ciphertext"])
                        access_token, rotated = appraisal.refresh(refresh_token)
                        claims = verify_access_token(access_token, jwks)
                        if claims["sub"] != row["external_subject"]:
                            raise ValueError("subject")
                        expires = datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
                        replace_tokens(conn, key, row["id"], access_token, rotated, expires)
                        conn.commit()
                    except Exception:
                        return _drop(conn, cookie, "Appraisal refresh failed. The OS session was deleted.")
                scopes = scopes_for(conn, row["external_subject"])
                view = session_view(row, scopes)
                conn.commit()
                return Resolved(session=view)
        except Exception:
            log.warning("The OS session could not be read.")
            return Resolved(clear_cookie=True)


def end_session(cookie: str, settings, appraisal, db) -> None:
    forget_session(cookie)
    if not cookie:
        return
    key = key_bytes(settings.token_key)
    try:
        with db() as conn:
            row = load_session_row(conn, cookie)
            if row is not None and key is not None:
                try:
                    access_token = unseal(key, row["id"], row["access_ciphertext"])
                    if not is_temp_token(access_token):
                        appraisal.logout(access_token)
                except Exception:
                    log.warning("Appraisal logout was not confirmed. The OS session was deleted.")
            delete_session(conn, cookie)
            conn.commit()
    except Exception:
        log.warning("The OS session could not be deleted.")
