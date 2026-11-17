"""Bind every company from the latest Business Central company probe into core/source.

Does not call Business Central. Use this so the product shell lists the full sandbox
directory even before a VPN pull loads ledgers. Run scripts/pull_books.py --scope all
when the sandbox is reachable to load data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import app_connection
from app.settings import load_settings
from app.sync_service import SyncService, entity_code_for


def main() -> int:
    settings = load_settings()
    service = SyncService(settings)
    with app_connection() as conn:
        instance = conn.execute(
            """
            SELECT i.id, i.code, i.base_url
            FROM source.instance i
            JOIN source.system s ON s.id = i.system_id
            WHERE s.code = 'BUSINESS_CENTRAL'
            ORDER BY i.id DESC
            LIMIT 1
            """
        ).fetchone()
        if instance is None:
            print("No Business Central instance row yet. Run a successful BC pull once first.")
            return 1
        probe = conn.execute(
            """
            SELECT companies, probed_at, message
            FROM source.company_probe
            WHERE instance_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (instance["id"],),
        ).fetchone()
        if probe is None:
            print("No company probe stored. Cannot bind without a listed company set.")
            return 1
        companies = probe["companies"]
        if isinstance(companies, str):
            companies = json.loads(companies)
        if not isinstance(companies, list) or not companies:
            print("Latest probe has no company list.")
            return 1
        print("instance", instance["code"], instance["base_url"])
        print("probe_at", probe["probed_at"])
        print("binding", len(companies), "companies")
        bound = []
        for row in companies:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            target = {
                "id": str(row["id"]),
                "name": row.get("displayName") or row.get("name") or "Company",
            }
            company = service._bind_company(conn, instance, target)
            code = entity_code_for(target["id"], target["name"])
            bound.append((code, target["name"], target["id"]))
            print(" ", code, target["name"], target["id"])
        conn.commit()
        # Bust shell cache so tabs refresh on next request.
        import app.queries as queries

        queries._SHELL_ENTITIES_CACHE = None
        print("bound", len(bound))
    return 0


if __name__ == "__main__":
    if not os.environ.get("VGGOS_SECRETS_FILE"):
        wsl_secrets = Path("/mnt/c/Users/Admin/AppData/Local/vggos/secrets.env")
        if wsl_secrets.is_file():
            os.environ["VGGOS_SECRETS_FILE"] = str(wsl_secrets)
    raise SystemExit(main())
