import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.allowlist import parse_allowlist
from app.db import connect
from app.iam_config import AuthKeyRefused, TokenRejected
from app.jwks import JwksCache, verify_access_token
from app.queries import REGISTER_SQL, UNMASKED_REGISTER_SQL
from app.rate_limit import RateLimiter
from app.session_store import key_bytes, unseal
from app.web import create_app
from tests.support_iam import (
    READER_EMAIL,
    appraisal_app,
    cleanup_identity,
    csrf_from,
    forge_api_key,
    iam_settings,
    mint,
    post_session,
    seed_grants,
    session_cookie,
    signed_in_client,
    signing_material,
)

SUBJECT = "22222222-2222-4222-8222-222222222222"
NO_GRANT_EMAIL = "nogrant@example.com"
CLASH_EMAIL = "clash@example.com"
BIND_EMAIL = "bind@example.com"
BIND_SUBJECT = "33333333-3333-4333-8333-333333333333"


def test_service_role_anon_key_refuses_the_process():
    settings = iam_settings(auth_anon_key=forge_api_key("service_role"))
    with pytest.raises(AuthKeyRefused):
        create_app(settings)


def test_anon_key_must_have_role_anon():
    settings = iam_settings(auth_anon_key=forge_api_key("authenticated"))
    with pytest.raises(AuthKeyRefused):
        create_app(settings)


def test_rejects_access_token_with_wrong_iss():
    private, document = signing_material()
    cache = JwksCache(loader=lambda: document)
    token = mint(private, SUBJECT, iss="https://other.example/auth/v1")
    with pytest.raises(TokenRejected) as caught:
        verify_access_token(token, cache)
    assert caught.value.code == "iss"


def test_rejects_access_token_with_wrong_aud():
    private, document = signing_material()
    cache = JwksCache(loader=lambda: document)
    token = mint(private, SUBJECT, aud="anon")
    with pytest.raises(TokenRejected) as caught:
        verify_access_token(token, cache)
    assert caught.value.code == "aud"


def test_rejects_access_token_with_wrong_alg():
    private, document = signing_material()
    now = int(time.time())
    token = jwt.encode(
        {"iss": "https://qnorggoycwbbxdlvbcvq.supabase.co/auth/v1", "aud": "authenticated", "sub": SUBJECT, "exp": now + 600},
        "not-a-signing-secret",
        algorithm="HS256",
        headers={"kid": "test-key", "alg": "HS256"},
    )
    cache = JwksCache(loader=lambda: document)
    with pytest.raises(TokenRejected) as caught:
        verify_access_token(token, cache)
    assert caught.value.code == "alg"
    assert private is not None


def test_rejects_access_token_with_expired_exp():
    private, document = signing_material()
    cache = JwksCache(loader=lambda: document)
    token = mint(private, SUBJECT, exp=int(time.time()) - 60)
    with pytest.raises(TokenRejected) as caught:
        verify_access_token(token, cache)
    assert caught.value.code == "exp"


def test_unknown_kid_refreshes_jwks():
    private, document = signing_material()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"keys": []}
        return document

    cache = JwksCache(loader=loader)
    claims = verify_access_token(mint(private, SUBJECT), cache)
    assert claims["sub"] == SUBJECT
    assert calls["n"] == 2


def test_login_without_finance_grant_is_forbidden():
    with appraisal_app(SUBJECT, NO_GRANT_EMAIL, seed=False) as (client, fake, settings, access, refresh, _private, _document):
        response = post_session(client, NO_GRANT_EMAIL)
        assert response.status_code == 403
        assert "no finance grant" in response.text
        assert session_cookie(response) == ""
        assert access not in response.text
        assert refresh not in response.text
        assert fake.password_attempts == 1
        with connect(settings.migration_database_url) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM iam.os_session s
                JOIN iam.principal p ON p.id = s.principal_id
                WHERE p.external_subject = %s
                """,
                (SUBJECT,),
            ).fetchone()
        assert row is None


def test_session_json_and_cookie_omit_appraisal_tokens():
    with appraisal_app("44444444-4444-4444-8444-444444444444", "json@example.com", ("books:read",)) as (
        client, fake, _settings, access, refresh, _private, _document,
    ):
        response = post_session(client, "json@example.com", as_json=True)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        cookie = session_cookie(response)
        assert cookie
        header = " ".join(response.headers.get_list("set-cookie")).lower()
        assert "httponly" in header
        assert "secure" in header
        assert "samesite=lax" in header
        assert access not in response.text
        assert refresh not in response.text
        assert access not in header
        assert refresh not in header
        assert fake.access not in cookie


def test_refresh_token_is_encrypted_at_rest_and_absent_from_logs(caplog):
    subject = "55555555-5555-4555-8555-555555555555"
    email = "encrypted@example.com"
    with appraisal_app(subject, email, ("books:read",)) as (client, fake, settings, access, _refresh, _private, _document):
        fake.refresh = "refresh-token-not-a-jwt"
        with caplog.at_level(logging.DEBUG):
            response = post_session(client, email, as_json=True)
        assert response.status_code == 200
        session_id = session_cookie(response)
        text = response.text + " ".join(response.headers.get_list("set-cookie")) + caplog.text
        assert "refresh-token-not-a-jwt" not in text
        assert access not in response.text
        assert "correct-horse" not in caplog.text
        with connect(settings.migration_database_url) as conn:
            row = conn.execute(
                "SELECT refresh_ciphertext, access_ciphertext FROM iam.os_session WHERE id = %s",
                (session_id,),
            ).fetchone()
        refresh_blob = bytes(row["refresh_ciphertext"])
        access_blob = bytes(row["access_ciphertext"])
        assert b"refresh-token-not-a-jwt" not in refresh_blob
        assert access.encode() not in access_blob
        assert unseal(key_bytes(settings.token_key), session_id, refresh_blob) == "refresh-token-not-a-jwt"


def test_canonical_email_failure_does_not_use_the_typed_address():
    with appraisal_app(SUBJECT, NO_GRANT_EMAIL, seed=False) as (client, fake, *_rest):
        fake.fail_canonical = True
        response = post_session(client, "old.name@peopleos.co")
        assert response.status_code == 401
        assert response.text.count("Those credentials were not accepted.") == 1
        assert fake.password_attempts == 0
        assert fake.canonical_calls == ["old.name@peopleos.co"]


def test_password_grant_400_is_generic():
    with appraisal_app(SUBJECT, NO_GRANT_EMAIL, ("books:read",)) as (client, fake, *_rest):
        fake.reject_password = True
        response = post_session(client, NO_GRANT_EMAIL)
        assert response.status_code == 401
        assert "Those credentials were not accepted." in response.text
        assert NO_GRANT_EMAIL not in response.text
        assert "invalid_grant" not in response.text
        assert session_cookie(response) == ""


def test_must_change_password_does_not_open_a_session():
    with appraisal_app(SUBJECT, "change@example.com", ("books:read",), name="Change Required") as (client, fake, settings, *_rest):
        fake.profile_row = {"must_change_password": True, "name": "Change Required", "email": "change@example.com"}
        response = post_session(client, "change@example.com")
        assert response.status_code == 403
        assert "Finish the password change on the appraisal site" in response.text
        assert session_cookie(response) == ""
        with connect(settings.migration_database_url) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM iam.os_session s
                JOIN iam.principal p ON p.id = s.principal_id
                WHERE p.external_subject = %s
                """,
                (SUBJECT,),
            ).fetchone()
        assert row is None


def test_allowlist_clash_is_left_pending():
    email = CLASH_EMAIL
    subject = "66666666-6666-4666-8666-666666666666"
    settings = iam_settings()
    try:
        seed_grants(settings, email, "First Person", ("books:read",))
        with connect(settings.migration_database_url) as conn:
            conn.execute(
                """
                INSERT INTO iam.finance_grant (email, display_name, scope, action)
                VALUES (%s, 'Second Person', 'books:read', 'VIEW')
                """,
                (email,),
            )
            conn.commit()
        with appraisal_app(subject, email, seed=False) as (client, _fake, _settings, *_rest):
            response = post_session(client, email)
            assert response.status_code == 403
            assert "left pending" in response.text
            assert session_cookie(response) == ""
            with connect(settings.migration_database_url) as conn:
                rows = conn.execute(
                    "SELECT status, external_subject FROM iam.finance_grant WHERE email = %s",
                    (email,),
                ).fetchall()
                clash = conn.execute("SELECT detail FROM iam.grant_clash WHERE email = %s", (email,)).fetchone()
            assert rows
            assert {row["status"] for row in rows} == {"PENDING"}
            assert {row["external_subject"] for row in rows} == {None}
            assert clash is not None
    finally:
        cleanup_identity(settings, email, subject)


def test_allowlist_email_binds_once_then_authorises_by_subject():
    subject = BIND_SUBJECT
    email = BIND_EMAIL
    other = "other.bind@example.com"
    settings = iam_settings()
    try:
        seed_grants(settings, email, "Bound Person", ("books:read", "bank:read"))
        with appraisal_app(subject, email, seed=False, name="Bound Person") as (client, fake, _settings, *_rest):
            first = post_session(client, email)
            assert first.status_code == 303
            with connect(settings.migration_database_url) as conn:
                conn.execute(
                    """
                    INSERT INTO iam.finance_grant (email, display_name, scope, action)
                    VALUES (%s, 'Someone Else', 'books:read', 'VIEW')
                    """,
                    (other,),
                )
                conn.commit()
            page = client.get("/")
            token = csrf_from(page.text)
            client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
            fake.profile_row = {"must_change_password": False, "name": "Bound Person", "email": other}
            second = post_session(client, other)
            assert second.status_code == 303
            books = client.get("/")
            assert books.status_code == 200
            with connect(settings.migration_database_url) as conn:
                bound = conn.execute(
                    "SELECT external_subject, status FROM iam.finance_grant WHERE email = %s ORDER BY scope",
                    (email,),
                ).fetchall()
                left = conn.execute(
                    "SELECT status, external_subject FROM iam.finance_grant WHERE email = %s",
                    (other,),
                ).fetchone()
            assert {row["external_subject"] for row in bound} == {subject}
            assert {row["status"] for row in bound} == {"BOUND"}
            assert left["status"] == "PENDING"
            assert left["external_subject"] is None
    finally:
        cleanup_identity(settings, email, subject)
        cleanup_identity(settings, other, subject)


def test_failed_refresh_deletes_the_os_session():
    subject = "77777777-7777-4777-8777-777777777777"
    email = "refresh@example.com"
    with appraisal_app(subject, email, ("books:read",), exp_offset=30) as (client, fake, settings, *_rest):
        assert post_session(client, email).status_code == 303
        fake.fail_refresh = True
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        with connect(settings.migration_database_url) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM iam.os_session s
                JOIN iam.principal p ON p.id = s.principal_id
                WHERE p.external_subject = %s
                """,
                (subject,),
            ).fetchone()
        assert row is None


def test_logout_deletes_the_os_session():
    subject = "88888888-8888-4888-8888-888888888888"
    with signed_in_client(scopes=("books:read",), email="logout@example.com", subject=subject) as (
        client, fake, settings, _access, _refresh,
    ):
        page = client.get("/")
        token = csrf_from(page.text)
        response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
        assert response.status_code == 303
        assert fake.logout_called is True
        assert client.get("/", follow_redirects=False).status_code == 303
        with connect(settings.migration_database_url) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM iam.os_session s
                JOIN iam.principal p ON p.id = s.principal_id
                WHERE p.external_subject = %s
                """,
                (subject,),
            ).fetchone()
        assert row is None


def test_loopback_http_keeps_an_httponly_session_cookie():
    subject = "12121212-1212-4212-8212-121212121212"
    email = "loopback@example.com"
    with appraisal_app(subject, email, ("books:read",)) as (client, _fake, _settings, access, refresh, _private, _document):
        local = TestClient(client.app, base_url="http://127.0.0.1:8096")
        page = local.get("/login", headers={"X-Forwarded-Proto": "https"})
        token = csrf_from(page.text)
        response = local.post(
            "/api/session",
            data={"email": email, "password": "correct-horse", "csrf_token": token},
            headers={"X-Forwarded-Proto": "https"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        line = next(item for item in response.headers.get_list("set-cookie") if item.startswith("vggos_session="))
        folded = line.lower()
        assert "httponly" in folded
        assert "samesite=lax" in folded
        assert "secure" not in folded
        assert access not in line
        assert refresh not in line
        assert local.get("/").status_code == 200


def test_sign_in_form_works_without_the_csrf_cookie():
    with appraisal_app(SUBJECT, NO_GRANT_EMAIL, seed=False) as (client, fake, *_rest):
        fake.reject_password = True
        page = client.get("/login")
        token = csrf_from(page.text)
        bare = TestClient(client.app, base_url="http://localhost:8096")
        response = bare.post(
            "/api/session",
            data={"email": NO_GRANT_EMAIL, "password": "correct-horse", "csrf_token": token},
            headers={"Origin": "http://localhost:8096"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "Reload the sign-in page" not in response.text
        assert fake.password_attempts == 1
        flipped = "1" if token[-1] != "1" else "2"
        forged = bare.post(
            "/api/session",
            data={"email": NO_GRANT_EMAIL, "password": "correct-horse", "csrf_token": token[:-1] + flipped},
            headers={"Origin": "http://localhost:8096"},
            follow_redirects=False,
        )
        assert forged.status_code == 403
        assert "Reload the sign-in page" in forged.text
        assert fake.password_attempts == 1


def test_api_session_is_the_only_login():
    settings = iam_settings()
    client = TestClient(create_app(settings), base_url="https://testserver")
    response = client.post("/login", data={"email": "a@example.com", "password": "secret", "csrf_token": "nope"})
    assert response.status_code == 405
    assert session_cookie(response) == ""


def test_cleartext_remote_sign_in_is_refused():
    with appraisal_app(SUBJECT, NO_GRANT_EMAIL, seed=False) as (client, fake, *_rest):
        remote = TestClient(client.app, base_url="http://evil.example")
        page = remote.get("/login")
        token = csrf_from(page.text)
        response = remote.post(
            "/api/session",
            data={"email": NO_GRANT_EMAIL, "password": "correct-horse", "csrf_token": token},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert fake.password_attempts == 0


def test_books_and_bank_screens_follow_grants():
    with signed_in_client(scopes=("books:read",), email="books-only@example.com", subject="99999999-9999-4999-8999-999999999999") as (
        client, *_rest,
    ):
        assert client.get("/").status_code == 200
        assert client.get("/bank-accounts").status_code == 403
        denied = client.get("/trial-balance.csv", params={"year": 2026, "month": 1})
        assert denied.status_code == 403
        trial = client.get("/trial-balance")
        assert "Export this month" not in trial.text


def test_sign_in_is_rate_limited_without_logging_the_body(caplog):
    limiter = RateLimiter(limit=2, window_seconds=60)
    with appraisal_app(SUBJECT, "limited@example.com", ("books:read",), limiter=limiter) as (client, fake, *_rest):
        fake.reject_password = True
        with caplog.at_level(logging.DEBUG):
            first = post_session(client, "limited@example.com", password="first-secret")
            second = post_session(client, "limited@example.com", password="second-secret")
            third = post_session(client, "limited@example.com", password="third-secret")
        assert first.status_code == 401
        assert second.status_code == 401
        assert third.status_code == 429
        assert fake.password_attempts == 2
        assert "first-secret" not in caplog.text
        assert "second-secret" not in caplog.text
        assert "third-secret" not in caplog.text


def test_allowlist_rejects_unmask_in_the_first_seed():
    with pytest.raises(ValueError):
        parse_allowlist([{"name": "Ada", "email": "ada@example.com", "scopes": ["bank:unmask"]}])


def test_allowlist_reports_two_names_for_one_email():
    _rows, clashes = parse_allowlist([
        {"name": "Ada", "email": "Ada@Example.com", "scopes": ["books:read"]},
        {"name": "Grace", "email": "ada@example.com", "scopes": ["books:read"]},
    ])
    assert clashes == ["ada@example.com"]


def test_register_query_unmask_is_opt_in():
    assert "bank_account_secret" not in REGISTER_SQL.lower()
    assert "iban" not in UNMASKED_REGISTER_SQL.lower()
    assert "bank_account_secret" in UNMASKED_REGISTER_SQL.lower()
