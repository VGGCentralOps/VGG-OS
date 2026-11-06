"""Folder tree for the finance files kept in this database.

The bytes are the supplied workbooks and decks. They are not ledger balances.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from psycopg import errors
from psycopg.types.json import Json

MAX_BYTES = 50 * 1024 * 1024
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
ALLOWED_TYPES = {
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}
MESSAGES = {
    "duplicate": "That name is already in this folder.",
    "empty": "Choose a file to add.",
    "folder": "That folder is not available.",
    "name": "Use a name without slashes.",
    "size": "That file is larger than 50 MB.",
    "type": "Use an Excel, PowerPoint, Word, PDF, CSV, or image file.",
}


def message_for(code: str | None) -> str:
    return MESSAGES.get(code or "", "")


def clean_name(value: str) -> str | None:
    name = (value or "").strip()
    if not name or name in {".", ".."} or len(name) > 240:
        return None
    if "/" in name or "\\" in name:
        return None
    if any(ord(character) < 32 for character in name):
        return None
    return name


def extension_mime(name: str) -> str | None:
    return ALLOWED_TYPES.get(Path(name).suffix.lower())


def content_disposition(name: str) -> str:
    from urllib.parse import quote

    ascii_name = "".join(
        character if 32 <= ord(character) < 127 and character not in {'"', "\\"} else "_"
        for character in name
    ) or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def size_label(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def parse_node_id(value: str | None) -> UUID | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return UUID(text)
    except ValueError:
        return None


def list_library(root: Path) -> list[dict]:
    """Folders first, then files, each group in case-insensitive name order."""
    rows: list[dict] = []

    def walk(directory: Path, prefix: str) -> None:
        entries = [
            child
            for child in directory.iterdir()
            if child.name.casefold() not in SKIP_NAMES and not child.name.startswith(".")
            and (child.is_dir() or child.is_file())
        ]
        ordered = sorted((child for child in entries if child.is_dir()), key=lambda child: child.name.casefold())
        ordered += sorted((child for child in entries if child.is_file()), key=lambda child: child.name.casefold())
        for index, child in enumerate(ordered, start=1):
            relative = f"{prefix}/{child.name}" if prefix else child.name
            rows.append(
                {
                    "path": relative,
                    "name": child.name,
                    "kind": "folder" if child.is_dir() else "file",
                    "sort_index": index,
                    "source": child,
                }
            )
            if child.is_dir():
                walk(child, relative)

    walk(root, "")
    return rows


def file_count(conn) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM finance.repository_node WHERE kind = 'file'"
    ).fetchone()
    return int(row["n"])


def children(conn, parent_id: UUID | None) -> list[dict]:
    return conn.execute(
        """
        SELECT id, name, kind, mime, size_bytes, sort_index
        FROM finance.repository_node
        WHERE parent_id IS NOT DISTINCT FROM %s
        ORDER BY CASE WHEN kind = 'folder' THEN 0 ELSE 1 END, sort_index, lower(name)
        """,
        (parent_id,),
    ).fetchall()


def folder_node(conn, node_id: UUID) -> dict | None:
    row = conn.execute(
        """
        SELECT id, parent_id, name, kind
        FROM finance.repository_node
        WHERE id = %s
        """,
        (node_id,),
    ).fetchone()
    if row is None or row["kind"] != "folder":
        return None
    return row


def breadcrumbs(conn, node_id: UUID) -> list[dict]:
    crumbs: list[dict] = []
    current: UUID | None = node_id
    seen: set[UUID] = set()
    while current is not None and current not in seen and len(crumbs) < 40:
        seen.add(current)
        row = conn.execute(
            "SELECT id, parent_id, name FROM finance.repository_node WHERE id = %s",
            (current,),
        ).fetchone()
        if row is None:
            break
        crumbs.append({"id": row["id"], "name": row["name"]})
        current = row["parent_id"]
    crumbs.reverse()
    return crumbs


def file_payload(conn, node_id: UUID) -> dict | None:
    return conn.execute(
        """
        SELECT n.name, n.mime, f.content
        FROM finance.repository_node n
        JOIN finance.repository_file f ON f.node_id = n.id
        WHERE n.id = %s AND n.kind = 'file'
        """,
        (node_id,),
    ).fetchone()


def _parent_is_folder(conn, parent_id: UUID | None) -> bool:
    if parent_id is None:
        return True
    return folder_node(conn, parent_id) is not None


def _next_sort(conn, parent_id: UUID | None) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(sort_index), 0) + 1 AS next_index
        FROM finance.repository_node
        WHERE parent_id IS NOT DISTINCT FROM %s
        """,
        (parent_id,),
    ).fetchone()
    return int(row["next_index"])


def _audit(conn, actor_id: int, action: str, node_id: UUID, details: dict) -> None:
    conn.execute(
        """
        INSERT INTO audit.event (actor_id, action, object_type, object_id, outcome, details)
        VALUES (%s, %s, 'repository_node', %s, 'SUCCESS', %s)
        """,
        (actor_id, action, str(node_id), Json(details)),
    )


def _find_sibling(conn, parent_id: UUID | None, name: str) -> dict | None:
    return conn.execute(
        """
        SELECT id, kind
        FROM finance.repository_node
        WHERE parent_id IS NOT DISTINCT FROM %s AND lower(name) = lower(%s)
        """,
        (parent_id, name),
    ).fetchone()


def add_folder(conn, parent_id: UUID | None, name: str, actor_id: int) -> str:
    cleaned = clean_name(name)
    if cleaned is None:
        return "name"
    if not _parent_is_folder(conn, parent_id):
        return "folder"
    try:
        row = conn.execute(
            """
            INSERT INTO finance.repository_node (parent_id, name, kind, sort_index)
            VALUES (%s, %s, 'folder', %s)
            RETURNING id
            """,
            (parent_id, cleaned, _next_sort(conn, parent_id)),
        ).fetchone()
    except errors.UniqueViolation:
        conn.rollback()
        return "duplicate"
    _audit(conn, actor_id, "REPOSITORY_ADD", row["id"], {"name": cleaned, "kind": "folder"})
    return ""


def add_file(conn, parent_id: UUID | None, name: str, content: bytes, actor_id: int) -> str:
    cleaned = clean_name(name)
    if cleaned is None:
        return "name"
    mime = extension_mime(cleaned)
    if mime is None:
        return "type"
    if not content:
        return "empty"
    if len(content) > MAX_BYTES:
        return "size"
    if not _parent_is_folder(conn, parent_id):
        return "folder"
    try:
        row = conn.execute(
            """
            INSERT INTO finance.repository_node (parent_id, name, kind, mime, size_bytes, sort_index)
            VALUES (%s, %s, 'file', %s, %s, %s)
            RETURNING id
            """,
            (parent_id, cleaned, mime, len(content), _next_sort(conn, parent_id)),
        ).fetchone()
        conn.execute(
            "INSERT INTO finance.repository_file (node_id, content) VALUES (%s, %s)",
            (row["id"], content),
        )
    except errors.UniqueViolation:
        conn.rollback()
        return "duplicate"
    _audit(
        conn,
        actor_id,
        "REPOSITORY_ADD",
        row["id"],
        {"name": cleaned, "kind": "file", "size_bytes": len(content)},
    )
    return ""


def delete_node(conn, node_id: UUID, actor_id: int) -> tuple[str, UUID | None]:
    row = conn.execute(
        """
        DELETE FROM finance.repository_node
        WHERE id = %s
        RETURNING parent_id, name, kind
        """,
        (node_id,),
    ).fetchone()
    if row is None:
        return "folder", None
    _audit(conn, actor_id, "REPOSITORY_DELETE", node_id, {"name": row["name"], "kind": row["kind"]})
    return "", row["parent_id"]


def ensure_library_node(conn, parent_id: UUID | None, item: dict, content: bytes | None) -> UUID:
    """Insert a library row, or reuse a folder that is already there. Existing files are left as they are."""
    found = _find_sibling(conn, parent_id, item["name"])
    if found is not None:
        if found["kind"] != item["kind"]:
            raise RuntimeError(f"{item['path']} is already stored as a {found['kind']}.")
        return found["id"]
    if item["kind"] == "folder":
        row = conn.execute(
            """
            INSERT INTO finance.repository_node (parent_id, name, kind, sort_index)
            VALUES (%s, %s, 'folder', %s)
            RETURNING id
            """,
            (parent_id, item["name"], item["sort_index"]),
        ).fetchone()
        return row["id"]
    row = conn.execute(
        """
        INSERT INTO finance.repository_node (parent_id, name, kind, mime, size_bytes, sort_index)
        VALUES (%s, %s, 'file', %s, %s, %s)
        RETURNING id
        """,
        (parent_id, item["name"], extension_mime(item["name"]), len(content or b""), item["sort_index"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO finance.repository_file (node_id, content) VALUES (%s, %s)",
        (row["id"], content),
    )
    return row["id"]
