from pathlib import Path

from fastapi.testclient import TestClient

from app.repository import clean_name, content_disposition, extension_mime, list_library, message_for
from app.web import create_app
from tests.support_iam import csrf_from, signed_in_client
from app.settings import load_settings


def test_library_order_skips_system_files(tmp_path: Path):
    (tmp_path / ".DS_Store").write_bytes(b"skip")
    (tmp_path / "2 February Report").mkdir()
    (tmp_path / "1 January Report").mkdir()
    january = tmp_path / "1 January Report"
    (january / "b.pptx").write_bytes(b"deck")
    (january / "Board Pack").mkdir()
    (january / "a.xlsx").write_bytes(b"book")

    rows = [(item["path"], item["kind"], item["sort_index"]) for item in list_library(tmp_path)]
    assert rows == [
        ("1 January Report", "folder", 1),
        ("1 January Report/Board Pack", "folder", 1),
        ("1 January Report/a.xlsx", "file", 2),
        ("1 January Report/b.pptx", "file", 3),
        ("2 February Report", "folder", 2),
    ]
    assert all(".DS_Store" not in path for path, _kind, _index in rows)


def test_names_and_types():
    assert clean_name("  Board Pack  ") == "Board Pack"
    assert clean_name("a/b") is None
    assert clean_name("a\\b") is None
    assert clean_name("..") is None
    assert extension_mime("Pack.PPTX") == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert extension_mime("notes.exe") is None
    assert "filename*=UTF-8''" in content_disposition("VGG Pack.pptx")
    assert message_for("type").startswith("Use an Excel")
    assert message_for("nope") == ""


def test_repositories_requires_sign_in():
    client = TestClient(create_app(load_settings()), base_url="https://testserver")
    assert client.get("/repositories", follow_redirects=False).status_code == 303


def test_repositories_page_and_rejected_upload():
    with signed_in_client() as (signed, *_rest):
        page = signed.get("/repositories")
        assert page.status_code == 200
        assert "Add a file here" in page.text
        assert "prepared files" in page.text
        token = csrf_from(page.text)
        rejected = signed.post(
            "/repositories/upload",
            data={"csrf_token": token},
            files={"upload": ("notes.exe", b"MZ", "application/octet-stream")},
            follow_redirects=False,
        )
        assert rejected.status_code == 303
        assert rejected.headers["location"].endswith("?error=type")
        named = signed.post(
            "/repositories/folder",
            data={"csrf_token": token, "name": "a/b"},
            follow_redirects=False,
        )
        assert named.status_code == 303
        assert named.headers["location"].endswith("?error=name")
