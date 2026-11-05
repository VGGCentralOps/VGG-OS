"""Load a finance allowlist into pending grants.

The file stays outside this repository. Login binds an email to a subject once.
This script does not bind anyone, and it refuses bank:unmask unless --allow-unmask is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.allowlist import insert_pending, parse_allowlist
from app.db import connect
from app.settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Load pending VGG OS finance grants.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--allow-unmask", action="store_true")
    args = parser.parse_args()
    if not args.path.is_file():
        raise SystemExit("Allowlist file was not found.")
    rows, clashes = parse_allowlist(json.loads(args.path.read_text(encoding="utf-8")), allow_unmask=args.allow_unmask)
    settings = load_settings()
    if not settings.migration_database_url:
        raise SystemExit("VGGOS_MIGRATION_DATABASE_URL is not set.")
    with connect(settings.migration_database_url) as conn:
        inserted = insert_pending(conn, rows)
        conn.commit()
    print(f"Pending grants inserted: {inserted}")
    for email in clashes:
        print(f"Left pending, more than one account for this email: {email}")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        sys.exit(str(exc))
