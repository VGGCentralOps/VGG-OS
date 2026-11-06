"""Load a local folder tree into finance.repository_node.

Existing files are left in place. A second run adds only what is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repository import clean_name, ensure_library_node, extension_mime, list_library
from app.settings import DEFAULT_SECRETS, read_env_file


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/load_repository.py <folder>")
    source = Path(sys.argv[1])
    if not source.is_dir():
        raise SystemExit(f"Folder not found: {source}")
    values = read_env_file(DEFAULT_SECRETS)
    url = values.get("VGGOS_MIGRATION_DATABASE_URL", "")
    if not url:
        raise SystemExit("VGGOS_MIGRATION_DATABASE_URL is missing from the secrets file.")
    items = list_library(source)
    for item in items:
        if clean_name(item["name"]) != item["name"]:
            raise SystemExit(f"Name cannot be stored: {item['path']}")
        if item["kind"] == "file" and extension_mime(item["name"]) is None:
            raise SystemExit(f"File type is not accepted: {item['path']}")
    folders: dict[str, object] = {"": None}
    added = 0
    kept = 0
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=20) as conn:
        conn.execute("SET statement_timeout = '10min'")
        for item in items:
            parent_path = item["path"].rsplit("/", 1)[0] if "/" in item["path"] else ""
            before = conn.execute("SELECT COUNT(*) AS n FROM finance.repository_node").fetchone()["n"]
            content = item["source"].read_bytes() if item["kind"] == "file" else None
            if content is not None and len(content) > 50 * 1024 * 1024:
                raise SystemExit(f"File is larger than 50 MB: {item['path']}")
            node_id = ensure_library_node(conn, folders[parent_path], item, content)
            after = conn.execute("SELECT COUNT(*) AS n FROM finance.repository_node").fetchone()["n"]
            if item["kind"] == "folder":
                folders[item["path"]] = node_id
            if after > before:
                added += 1
                print(f"added {item['kind']} {item['path']}")
            else:
                kept += 1
                print(f"kept {item['kind']} {item['path']}")
        conn.commit()
    print(f"Done. Added {added}. Already stored {kept}.")


if __name__ == "__main__":
    main()
