"""Create the temporary database sign-ins. Passwords stay in the secrets file, not in git.

Re-running keeps a password that is already in the secrets file and refreshes the hash.
"""

from __future__ import annotations

import os
import secrets
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
    "email_key": "VGGOS_TEMP_ADMIN_EMAIL",
    "email": "admin@vggos.local",
    "name": "Local admin",
    "subject": "temp-db:admin",
    "default_password": "vggos-local",
}
EXECUTIVE = {
    "password_key": "VGGOS_TEMP_EXECUTIVE_PASSWORD",
    "email_key": "VGGOS_TEMP_EXECUTIVE_EMAIL",
    "email": "executive@vggos.local",
    "name": "Local executive",
    "subject": "temp-db:executive",
}
REMOVED_EMAILS = ("temp.operator@vggos.local", "temp.treasury@vggos.local", "ceo@vgg.tools")


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
    updates[ACCOUNT["email_key"]] = ACCOUNT["email"]
    executive_password = values.get(EXECUTIVE["password_key"], "")
    if len(executive_password) < 12:
        executive_password = new_password()
    updates[EXECUTIVE["password_key"]] = executive_password
    updates[EXECUTIVE["email_key"]] = EXECUTIVE["email"]
    upsert_env(SECRETS, updates)
    with psycopg.connect(url) as conn:
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
        # Rebind if an older seed used the same subject under a different email.
        conn.execute("DELETE FROM iam.temp_credential WHERE external_subject = %s AND email <> %s", (EXECUTIVE["subject"], EXECUTIVE["email"]))
        conn.execute("DELETE FROM iam.finance_grant WHERE external_subject = %s AND email <> %s", (EXECUTIVE["subject"], EXECUTIVE["email"]))
        seed_account(conn, ACCOUNT["email"], ACCOUNT["name"], ACCOUNT["subject"], password)
        seed_account(conn, EXECUTIVE["email"], EXECUTIVE["name"], EXECUTIVE["subject"], executive_password)
        conn.commit()
    print("Temporary database sign-in is ready.")
    print(ACCOUNT["email"])
    print(f"Password is in {SECRETS} as {ACCOUNT['password_key']}")
    print(EXECUTIVE["email"])
    print(f"Password is in {SECRETS} as {EXECUTIVE['password_key']}")


def new_password() -> str:
    """A password that can be read aloud. Not written into git."""
    words = (
        "Amber", "Birch", "Cedar", "Delta", "Ember", "Fjord", "Granite", "Harbor",
        "Indigo", "Juniper", "Kelvin", "Linden", "Marble", "North", "Orchid", "Pebble",
        "Quartz", "Ridge", "Summit", "Timber",
    )
    first = secrets.choice(words)
    second = secrets.choice(words)
    while second == first:
        second = secrets.choice(words)
    return f"{first}-{second}-{secrets.randbelow(90) + 10}"


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
