"""Test doubles for appraisal sign-in. Tokens here are generated in the test, not copied from a live session."""

from __future__ import annotations

import base64
import json
import re
import time
from contextlib import contextmanager

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

from app.appraisal import AuthRejected, CanonicalEmailFailed, RefreshFailed
from app.db import connect
from app.iam_config import EXPECTED_AUD, EXPECTED_ISS, SCOPE_ACTION
from app.jwks import JwksCache
from app.settings import Settings, load_settings
from app.web import create_app

TOKEN_KEY = "ab" * 32
READER_SUBJECT = "11111111-1111-4111-8111-111111111111"
READER_EMAIL = "reader@example.com"


def forge_api_key(role: str) -> str:
    def part(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part({'role': role})}.sig"


def signing_material():
    private = ec.generate_private_key(ec.SECP256R1())
    encoded = ECAlgorithm.to_jwk(private.public_key())
    jwk = json.loads(encoded) if isinstance(encoded, str) else encoded
    jwk.update({"kid": "test-key", "alg": "ES256", "use": "sig"})
    return private, {"keys": [jwk]}


def mint(private, subject: str, **overrides) -> str:
    now = int(time.time())
    body = {
        "iss": EXPECTED_ISS,
        "aud": EXPECTED_AUD,
        "sub": subject,
        "role": "authenticated",
        "exp": now + 3600,
        "iat": now,
    }
    body.update(overrides)
    return jwt.encode(body, private, algorithm="ES256", headers={"kid": "test-key", "alg": "ES256"})


class FakeAppraisal:
    def __init__(self, access: str, refresh: str):
        self.access = access
        self.refresh = refresh
        self.canonical_calls: list[str] = []
        self.password_emails: list[str] = []
        self.password_attempts = 0
        self.fail_canonical = False
        self.reject_password = False
        self.profile_row = {"must_change_password": False, "name": "Finance Reader", "email": READER_EMAIL}
        self.fail_refresh = False
        self.logout_called = False
        self.resolved = None

    def canonical_email(self, typed: str) -> str:
        self.canonical_calls.append(typed)
        if self.fail_canonical:
            raise CanonicalEmailFailed()
        return self.resolved or typed

    def password_grant(self, email: str, password: str):
        self.password_attempts += 1
        self.password_emails.append(email)
        if self.reject_password:
            raise AuthRejected()
        return self.access, self.refresh

    def profile(self, subject: str, access_token: str):
        if self.profile_row is None:
            return None
        return dict(self.profile_row)

    def refresh(self, refresh_token: str):
        if self.fail_refresh:
            raise RefreshFailed()
        return self.access, self.refresh

    def logout(self, access_token: str) -> None:
        self.logout_called = True


def iam_settings(**overrides) -> Settings:
    values = load_settings().__dict__.copy()
    values["auth_url"] = "https://qnorggoycwbbxdlvbcvq.supabase.co"
    values["auth_anon_key"] = forge_api_key("anon")
    values["token_key"] = TOKEN_KEY
    # Tests exercise appraisal grant flow unless a case overrides this.
    values["appraisal_login"] = True
    values.update(overrides)
    return Settings(**values)


def seed_grants(settings: Settings, email: str, name: str, scopes: tuple[str, ...], subject: str | None = None) -> None:
    with connect(settings.migration_database_url) as conn:
        conn.execute("DELETE FROM iam.grant_clash WHERE email = %s", (email,))
        conn.execute("DELETE FROM iam.finance_grant WHERE email = %s", (email,))
        for scope in scopes:
            if subject:
                conn.execute(
                    """
                    INSERT INTO iam.finance_grant
                      (email, display_name, scope, action, external_subject, status, bound_at)
                    VALUES (%s, %s, %s, %s, %s, 'BOUND', clock_timestamp())
                    """,
                    (email, name, scope, SCOPE_ACTION[scope], subject),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO iam.finance_grant (email, display_name, scope, action)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (email, name, scope, SCOPE_ACTION[scope]),
                )
        conn.commit()


def cleanup_identity(settings: Settings, email: str, subject: str) -> None:
    with connect(settings.migration_database_url) as conn:
        conn.execute(
            """
            DELETE FROM iam.os_session
            WHERE principal_id IN (SELECT id FROM iam.principal WHERE external_subject = %s)
            """,
            (subject,),
        )
        conn.execute("DELETE FROM iam.grant_clash WHERE email = %s OR attempted_subject = %s", (email, subject))
        conn.execute("DELETE FROM iam.finance_grant WHERE email = %s OR external_subject = %s", (email, subject))
        conn.commit()


def csrf_from(page_text: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', page_text).group(1)


def post_session(client: TestClient, email: str, password: str = "correct-horse", *, as_json: bool = False):
    page = client.get("/login")
    token = csrf_from(page.text)
    if as_json:
        return client.post(
            "/api/session",
            json={"email": email, "password": password, "csrf_token": token},
            headers={"Accept": "application/json", "Origin": "http://127.0.0.1:8096"},
        )
    return client.post(
        "/api/session",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )


def session_cookie(response) -> str:
    for line in response.headers.get_list("set-cookie"):
        if line.startswith("vggos_session="):
            return line.split(";", 1)[0].split("=", 1)[1]
    return ""


@contextmanager
def appraisal_app(
    subject: str,
    email: str,
    scopes: tuple[str, ...] = (),
    name: str = "Finance Reader",
    *,
    seed: bool = True,
    exp_offset: int = 3600,
    limiter=None,
):
    private, document = signing_material()
    access = mint(private, subject, exp=int(time.time()) + exp_offset)
    refresh = f"refresh-token-{subject}"
    fake = FakeAppraisal(access, refresh)
    fake.profile_row = {"must_change_password": False, "name": name, "email": email}
    settings = iam_settings()
    if seed:
        seed_grants(settings, email, name, scopes)
    cache = JwksCache(loader=lambda: document)
    app = create_app(settings, appraisal=fake, jwks=cache, limiter=limiter)
    client = TestClient(app, base_url="https://testserver")
    try:
        yield client, fake, settings, access, refresh, private, document
    finally:
        cleanup_identity(settings, email, subject)


@contextmanager
def signed_in_client(
    scopes=("books:read", "bank:read", "export", "books:sync", "files:write"),
    email=READER_EMAIL,
    subject=READER_SUBJECT,
    name="Finance Reader",
):
    with appraisal_app(subject, email, scopes, name) as (client, fake, settings, access, refresh, private, document):
        response = post_session(client, email)
        assert response.status_code == 303, response.text
        yield client, fake, settings, access, refresh
