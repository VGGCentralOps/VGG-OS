"""Temporary database sign-in. These accounts must not call the appraisal project."""

from app.db import connect
from tests.support_iam import appraisal_app, csrf_from, iam_settings, post_session, session_cookie

EMAIL = "temp.reader@vggos.local"
SUBJECT = "temp-db:test-reader"
PASSWORD = "local-temp-secret-value"
NAME = "Temporary reader"
APPRAISAL_SUBJECT = "66666666-6666-4666-8666-666666666666"


def _prepare(settings) -> None:
    from app.temp_login import seed_account

    with connect(settings.migration_database_url) as conn:
        seed_account(conn, EMAIL, NAME, SUBJECT, PASSWORD, ("books:read", "bank:read"))
        conn.commit()


def _cleanup(settings) -> None:
    with connect(settings.migration_database_url) as conn:
        conn.execute(
            """
            DELETE FROM iam.os_session
            WHERE principal_id IN (SELECT id FROM iam.principal WHERE external_subject = %s)
            """,
            (SUBJECT,),
        )
        conn.execute("DELETE FROM iam.finance_grant WHERE email = %s OR external_subject = %s", (EMAIL, SUBJECT))
        conn.execute("DELETE FROM iam.temp_credential WHERE email = %s", (EMAIL,))
        conn.commit()


def test_temp_db_login_opens_a_session_without_appraisal(monkeypatch):
    monkeypatch.setenv("VGGOS_TEMP_DB_LOGIN", "1")
    settings = iam_settings(temp_db_login=True)
    _prepare(settings)
    try:
        with appraisal_app(SUBJECT, EMAIL, seed=False) as (client, fake, app_settings, access, refresh, _private, _document):
            fake.fail_refresh = True
            response = post_session(client, EMAIL, PASSWORD)
            assert response.status_code == 303
            assert session_cookie(response)
            assert fake.password_attempts == 0
            assert PASSWORD not in response.text
            assert access not in response.text
            assert refresh not in response.text
            cookie = session_cookie(response)
            with connect(app_settings.migration_database_url) as conn:
                conn.execute(
                    "UPDATE iam.os_session SET access_expires_at = now() - interval '5 minutes' WHERE id = %s",
                    (cookie,),
                )
                conn.commit()
            home = client.get("/")
            assert home.status_code == 200
            assert "Temporary reader" in home.text
            assert client.get("/bank-accounts").status_code == 200
            page = client.get("/books")
            token = csrf_from(page.text)
            logged_out = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
            assert logged_out.status_code == 303
            assert fake.logout_called is False
    finally:
        _cleanup(settings)


def test_temp_db_login_rejects_a_wrong_password_without_appraisal(monkeypatch):
    monkeypatch.setenv("VGGOS_TEMP_DB_LOGIN", "1")
    settings = iam_settings(temp_db_login=True)
    _prepare(settings)
    try:
        with appraisal_app(SUBJECT, EMAIL, seed=False) as (client, fake, _app_settings, _access, _refresh, _private, _document):
            response = post_session(client, EMAIL, "not-the-password")
            assert response.status_code == 401
            assert "Those credentials were not accepted." in response.text
            assert fake.password_attempts == 0
            assert session_cookie(response) == ""
    finally:
        _cleanup(settings)


def test_temp_db_login_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("VGGOS_TEMP_DB_LOGIN", "0")
    settings = iam_settings(temp_db_login=False, appraisal_login=True)
    _prepare(settings)
    try:
        with appraisal_app(APPRAISAL_SUBJECT, EMAIL, seed=False) as (client, fake, _app_settings, _access, _refresh, _private, _document):
            response = post_session(client, EMAIL, PASSWORD)
            assert fake.password_attempts == 1
            assert response.status_code == 403
    finally:
        _cleanup(settings)


def test_unknown_email_still_uses_appraisal_sign_in():
    with appraisal_app("55555555-5555-4555-8555-555555555555", "still-appraisal@example.com", ("books:read",)) as (
        client, fake, _settings, _access, _refresh, _private, _document,
    ):
        response = post_session(client, "still-appraisal@example.com")
        assert response.status_code == 303
        assert fake.password_attempts == 1
