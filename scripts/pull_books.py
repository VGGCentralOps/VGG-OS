"""Run a Business Central books pull into VGG OS. Must run on a host that can reach the instance.

Example from Ubuntu while the VPN tunnel is up:

  cd "/mnt/c/Users/Admin/Downloads/VGG OS"
  VGGOS_SECRETS_FILE=/mnt/c/Users/Admin/AppData/Local/vggos/secrets.env \\
    python3 scripts/pull_books.py --from 2023-12-01 --to 2026-09-30 --check-month 2025-01
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
    parser = argparse.ArgumentParser(description="Read-only Business Central books pull into VGG OS.")
    parser.add_argument("--from", dest="date_from", default="2023-12-01", help="Posting date from (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", default="2026-09-30", help="Posting date to (YYYY-MM-DD)")
    parser.add_argument("--check-month", default="2025-01", help="Reconciliation month YYYY-MM")
    parser.add_argument(
        "--scope",
        choices=("all", "one"),
        default="all",
        help="all companies on the instance, or only the configured default company",
    )
    args = parser.parse_args()
    date_from = date.fromisoformat(args.date_from)
    date_to = date.fromisoformat(args.date_to)
    year_text, month_text = args.check_month.split("-", 1)
    year, month = int(year_text), int(month_text)
    settings = load_settings()
    fail_interrupted_runs(settings.database_url)
    print("starting books pull")
    print("scope", args.scope)
    print("range", date_from.isoformat(), "to", date_to.isoformat())
    print("check_month", f"{year:04d}-{month:02d}")
    service = SyncService(settings)
    if args.scope == "all":
        outcome = service.run_all_companies(date_from, date_to, year, month)
    else:
        outcome = service.run_advancly(date_from, date_to, year, month)
    print("status", outcome.status)
    print("message", outcome.message)
    if outcome.companies:
        print("companies", len(outcome.companies))
        for company in outcome.companies:
            print(" ", company.get("name") or "?", company.get("id"))
    return 0 if outcome.status in {"finished", "already_running"} else 1


if __name__ == "__main__":
    if not os.environ.get("VGGOS_SECRETS_FILE"):
        wsl_secrets = Path("/mnt/c/Users/Admin/AppData/Local/vggos/secrets.env")
        if wsl_secrets.is_file():
            os.environ["VGGOS_SECRETS_FILE"] = str(wsl_secrets)
    raise SystemExit(main())
