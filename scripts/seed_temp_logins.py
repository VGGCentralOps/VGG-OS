"""Create the temporary database sign-ins. Passwords stay in the secrets file, not in git.

Re-running keeps a password that is already in the secrets file and refreshes the hash.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.temp_login import seed_account

SECRETS = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "vggos" / "secrets.env"

ACCOUNT = {
    "password_key": "VGGOS_TEMP_ADMIN_PASSWORD",
    "email": "admin@vggos.local",
    "name": "Local admin",
    "subject": "temp-db:admin",
    "default_password": "vggos-local",
}
REMOVED_EMAILS = ("temp.operator@vggos.local", "temp.treasury@vggos.local")


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> None:
    values = read_env(SECRETS)
    url = values.get("VGGOS_MIGRATION_DATABASE_URL", "")
    if not url:
        raise SystemExit(f"VGGOS_MIGRATION_DATABASE_URL is missing from {SECRETS}")
    updates: dict[str, str] = {"VGGOS_TEMP_DB_LOGIN": values.get("VGGOS_TEMP_DB_LOGIN") or "1"}
    current = values.get(ACCOUNT["password_key"], "")
    password = current if len(current) >= 8 else ACCOUNT["default_password"]
    updates[ACCOUNT["password_key"]] = password
    updates["VGGOS_TEMP_ADMIN_EMAIL"] = ACCOUNT["email"]
    upsert_env(SECRETS, updates)
    with psycopg.connect(url) as conn:
        seed_account(conn, ACCOUNT["email"], ACCOUNT["name"], ACCOUNT["subject"], password)
        for email in REMOVED_EMAILS:
            conn.execute(
                """
                DELETE FROM iam.os_session
                WHERE principal_id IN (SELECT id FROM iam.principal WHERE email = %s)
                """,
                (email,),
            )
            conn.execute("DELETE FROM iam.finance_grant WHERE email = %s", (email,))
            conn.execute("DELETE FROM iam.temp_credential WHERE email = %s", (email,))
        conn.commit()
    print("Temporary database sign-in is ready.")
    print(ACCOUNT["email"])
    print(f"Password is in {SECRETS} as {ACCOUNT['password_key']}")


def upsert_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    rewritten: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rewritten.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            rewritten.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            rewritten.append(line)
    for key, value in updates.items():
        if key not in seen:
            rewritten.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
