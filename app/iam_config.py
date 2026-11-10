"""Appraisal issuer and the browser origins for this service.

These origins are where the VGG OS pages run. They are not appraisal redirect
URLs. Password reset stays on the appraisal site.
"""

from __future__ import annotations

import base64
import json

APPRAISAL_PROJECT_REF = "qnorggoycwbbxdlvbcvq"
AUTH_BASE = f"https://{APPRAISAL_PROJECT_REF}.supabase.co"
EXPECTED_ISS = f"{AUTH_BASE}/auth/v1"
EXPECTED_AUD = "authenticated"
JWKS_URL = f"{EXPECTED_ISS}/.well-known/jwks.json"
JWKS_TTL_SECONDS = 300

# Host-only cookie. Do not set Domain=.vgg.tools.
SESSION_COOKIE = "vggos_session"
CSRF_COOKIE = "vggos_csrf"

# Not added to the appraisal redirect allowlist.
BROWSER_ORIGINS = (
    "http://127.0.0.1:8096",
    "http://localhost:8096",
    "https://vggos.vgg.tools",
    "https://vgg-os.onrender.com",
)

SCOPE_ACTION = {
    "books:read": "VIEW",
    "bank:read": "VIEW",
    "export": "EXPORT",
    "bank:unmask": "UNMASK",
    # Write permissions. Reading a screen never implies changing data.
    "books:sync": "SYNC",
    "files:write": "WRITE",
}


class AuthKeyRefused(Exception):
    """The process must not start with this API key."""


class TokenRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def anon_key_role(key: str) -> str | None:
    parts = (key or "").split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    padding = "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    role = payload.get("role") if isinstance(payload, dict) else None
    return role if isinstance(role, str) else None


def require_anon_key(key: str) -> None:
    """Trust the key only when its payload role is anon. A service role stops the process."""
    if not key:
        return
    role = anon_key_role(key)
    if role == "service_role":
        raise AuthKeyRefused("VGG_AUTH_ANON_KEY has role service_role. Refusing to start.")
    if role != "anon":
        raise AuthKeyRefused("VGG_AUTH_ANON_KEY must be an anon API key. Refusing to start.")
