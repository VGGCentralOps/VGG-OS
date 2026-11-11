"""Create the permitted database sign-ins. Passwords stay in the secrets file, not in git.

Re-running keeps a password that is already in the secrets file and refreshes the hash.
Only these accounts can sign in while appraisal login stays off.
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

# Kept for local/dev continuity.
ADMIN = {
    "password_key": "VGGOS_TEMP_ADMIN_PASSWORD",
    "email_key": "VGGOS_TEMP_ADMIN_EMAIL",
    "email": "admin@vggos.local",
    "name": "Local admin",
    "subject": "temp-db:admin",
    "default_password": "vggos-local",
}

# Production operator — the one permitted account for starters.
OPERATOR = {
    "password_key": "VGGOS_OPERATOR_PASSWORD",
    "email_key": "VGGOS_OPERATOR_EMAIL",
    "email": "ops@vggos.vgg.tools",
    "name": "VGG OS Operator",
    "subject": "temp-db:operator",
}

REMOVED_EMAILS = (
    "temp.operator@vggos.local",
    "temp.treasury@vggos.local",
    "ceo@vgg.tools",
    "executive@vggos.local",
)
REMOVED_SUBJECTS = ("temp-db:executive",)


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

    updates: dict[str, str] = {
        "VGGOS_TEMP_DB_LOGIN": values.get("VGGOS_TEMP_DB_LOGIN") or "1",
        # Appraisal password grants stay off until a real allowlist is loaded.
        "VGGOS_APPRAISAL_LOGIN": values.get("VGGOS_APPRAISAL_LOGIN") or "0",
    }

    admin_password = values.get(ADMIN["password_key"], "")
    if len(admin_password) < 8:
        admin_password = ADMIN["default_password"]
    updates[ADMIN["password_key"]] = admin_password
    updates[ADMIN["email_key"]] = ADMIN["email"]

    operator_password = values.get(OPERATOR["password_key"], "")
    if len(operator_password) < 16:
        operator_password = new_password()
    updates[OPERATOR["password_key"]] = operator_password
    updates[OPERATOR["email_key"]] = OPERATOR["email"]

    upsert_env(SECRETS, updates)

    with psycopg.connect(url) as conn:
        for email in REMOVED_EMAILS:
            _purge_email(conn, email)
        for subject in REMOVED_SUBJECTS:
            conn.execute("DELETE FROM iam.temp_credential WHERE external_subject = %s", (subject,))
            conn.execute("DELETE FROM iam.finance_grant WHERE external_subject = %s", (subject,))

        # Keep only the two permitted accounts active.
        conn.execute(
            """
            UPDATE iam.temp_credential
            SET active = false
            WHERE email NOT IN (%s, %s)
            """,
            (ADMIN["email"], OPERATOR["email"]),
        )
        seed_account(conn, ADMIN["email"], ADMIN["name"], ADMIN["subject"], admin_password)
        seed_account(conn, OPERATOR["email"], OPERATOR["name"], OPERATOR["subject"], operator_password)
        conn.commit()

    print("Permitted sign-in accounts are ready.")
    print(f"{ADMIN['email']}  (password in {SECRETS} as {ADMIN['password_key']})")
    print(f"{OPERATOR['email']}  (password in {SECRETS} as {OPERATOR['password_key']})")
    print("Appraisal password login is off (VGGOS_APPRAISAL_LOGIN=0).")


def _purge_email(conn, email: str) -> None:
    conn.execute(
        """
        DELETE FROM iam.os_session
        WHERE principal_id IN (SELECT id FROM iam.principal WHERE email = %s)
        """,
        (email,),
    )
    conn.execute("DELETE FROM iam.finance_grant WHERE email = %s", (email,))
    conn.execute("DELETE FROM iam.temp_credential WHERE email = %s", (email,))


def new_password() -> str:
    """A password that can be read aloud. Not written into git."""
    words = (
        "Amber", "Birch", "Cedar", "Delta", "Ember", "Fjord", "Granite", "Harbor",
        "Indigo", "Juniper", "Kelvin", "Linden", "Marble", "North", "Orchid", "Pebble",
        "Quartz", "Ridge", "Summit", "Timber", "Atlas", "Beacon", "Canyon", "Drift",
    )
    first = secrets.choice(words)
    second = secrets.choice(words)
    while second == first:
        second = secrets.choice(words)
    third = secrets.choice(words)
    while third in {first, second}:
        third = secrets.choice(words)
    return f"{first}-{second}-{third}-{secrets.randbelow(900) + 100}"


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
