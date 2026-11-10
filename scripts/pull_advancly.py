"""Run the Advancly Business Central pull. Must run on a host that can reach BCTEST.

Example from Ubuntu while the VPN tunnel is up:

  cd "/mnt/c/Users/Admin/Downloads/VGG OS"
  VGGOS_SECRETS_FILE=/mnt/c/Users/Admin/AppData/Local/vggos/secrets.env \\
    python3 scripts/pull_advancly.py --from 2024-01-01 --to 2026-12-31 --check-month 2026-01
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import load_settings
from app.sync_service import SyncService, fail_interrupted_runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Advancly Business Central pull into VGG OS.")
    parser.add_argument("--from", dest="date_from", required=True, help="Posting date from (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", required=True, help="Posting date to (YYYY-MM-DD)")
    parser.add_argument("--check-month", required=True, help="Reconciliation month YYYY-MM")
    args = parser.parse_args()
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    year_text, month_text = args.check_month.split("-", 1)
    year, month = int(year_text), int(month_text)
    settings = load_settings()
    fail_interrupted_runs(settings.database_url)
    print("starting Advancly pull")
    print("range", date_from.isoformat(), "to", date_to.isoformat())
    print("check_month", f"{year:04d}-{month:02d}")
    outcome = SyncService(settings).run_advancly(date_from, date_to, year, month)
    print("status", outcome.status)
    print("message", outcome.message)
    return 0 if outcome.status in {"finished", "already_running"} else 1


if __name__ == "__main__":
    # Allow the secrets path used under WSL.
    if not os.environ.get("VGGOS_SECRETS_FILE"):
        wsl_secrets = Path("/mnt/c/Users/Admin/AppData/Local/vggos/secrets.env")
        if wsl_secrets.is_file():
            os.environ["VGGOS_SECRETS_FILE"] = str(wsl_secrets)
    raise SystemExit(main())
