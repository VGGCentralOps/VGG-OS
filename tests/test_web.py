from fastapi.testclient import TestClient

from app.settings import load_settings
from app.web import create_app
from tests.support_iam import csrf_from, signed_in_client


def client():
    return TestClient(create_app(load_settings()), base_url="https://testserver")


def test_health_does_not_reveal_secrets():
    settings = load_settings()
    response = client().get("/health")
    assert response.status_code == 200
    text = response.text
    if settings.dev_password:
        assert settings.dev_password not in text
    if settings.auth_anon_key:
        assert settings.auth_anon_key not in text
    if settings.token_key:
        assert settings.token_key not in text
    assert settings.session_secret not in text
    assert "not_configured" in text
    assert "0.3.0" in text


def test_pages_require_a_session_and_do_not_invent_figures():
    test_client = client()
    assert test_client.get("/", follow_redirects=False).status_code == 303
    assert test_client.post("/bank-feeds/mono/pull").status_code == 401
    with signed_in_client() as (signed, _fake, _settings, _access, _refresh):
        books = signed.get("/")
        assert books.status_code == 200
        assert "Elijah" not in books.text
        assert 'href="/reports"' in books.text
        assert "Repositories" in books.text
        assert "Bank movements" in books.text
        assert "10.250.50.9" not in books.text
        trial = signed.get("/trial-balance")
        assert "No month has been loaded" in trial.text or "Trial balance" in trial.text
        banks = signed.get("/bank-accounts")
        assert "No bank accounts are loaded" in banks.text or "Bank accounts" in banks.text
        assert "dormant" in banks.text.lower()
        movements = signed.get("/bank-movements")
        assert movements.status_code == 200
        assert "No bank ledger entries are loaded" in movements.text or "Movements" in movements.text
        reports = signed.get("/reports", follow_redirects=False)
        assert reports.status_code == 200
        assert "Reports" in reports.text
        closed = signed.post("/bank-feeds/mono/pull")
        assert closed.status_code == 423
        assert "No balance is stored" in closed.text


def test_sync_post_does_not_start_a_call(monkeypatch):
    def explode(self, *args, **kwargs):
        raise AssertionError("Business Central sync started")

    monkeypatch.setattr("app.web.SyncService.run_advancly", explode)
    with signed_in_client() as (signed, *_rest):
        page = signed.get("/")
        token = csrf_from(page.text)
        response = signed.post(
            "/sync",
            data={"csrf_token": token, "date_from": "2026-01-01", "date_to": "2026-01-31", "year": "2026", "month": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_read_only_grants_cannot_sync_or_write_files():
    with signed_in_client(scopes=("books:read", "bank:read")) as (signed, *_rest):
        home = signed.get("/")
        assert home.status_code == 200
        assert "Your account can view runs but cannot start a pull" not in home.text
        books = signed.get("/books")
        assert "cannot start a pull" in books.text
        token = csrf_from(books.text) if 'name="csrf_token"' in books.text else csrf_from(home.text)
        sync = signed.post(
            "/sync",
            data={"csrf_token": token, "date_from": "2026-01-01", "date_to": "2026-01-31", "year": "2026", "month": "1"},
            follow_redirects=False,
        )
        assert sync.status_code == 403
        repos = signed.get("/repositories")
        assert "cannot upload" in repos.text
        write = signed.post(
            "/repositories/folder",
            data={"csrf_token": csrf_from(repos.text), "name": "Board Pack"},
            follow_redirects=False,
        )
        assert write.status_code == 403
