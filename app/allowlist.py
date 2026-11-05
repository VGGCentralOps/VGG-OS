"""Turn an allowlist document into pending finance grants. Nothing is bound here."""

from __future__ import annotations

from app.iam_config import SCOPE_ACTION


def normalise_email(value: str) -> str:
    text = (value or "").strip().lower()
    if "@" not in text or len(text) > 320 or any(char.isspace() for char in text):
        return ""
    return text


def parse_allowlist(payload, *, allow_unmask: bool = False) -> tuple[list[dict], list[str]]:
    if isinstance(payload, dict):
        people = payload.get("people", [])
    elif isinstance(payload, list):
        people = payload
    else:
        raise ValueError("The allowlist must be a list of people.")
    if not isinstance(people, list):
        raise ValueError("The allowlist must be a list of people.")

    rows: list[dict] = []
    names_by_email: dict[str, set[str]] = {}
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("Each allowlist entry must be an object.")
        email = normalise_email(str(person.get("email") or ""))
        name = str(person.get("name") or person.get("display_name") or "").strip()
        scopes = person.get("scopes") or []
        if not email or not name:
            raise ValueError("Each allowlist entry needs a name and an email.")
        if not isinstance(scopes, list) or not scopes:
            raise ValueError("Each allowlist entry needs at least one scope.")
        names_by_email.setdefault(email, set()).add(name.lower())
        for scope in scopes:
            if scope not in SCOPE_ACTION:
                raise ValueError("Unknown scope.")
            if scope == "bank:unmask" and not allow_unmask:
                raise ValueError("bank:unmask is not part of the first seed.")
            rows.append({
                "email": email,
                "display_name": name,
                "scope": scope,
                "action": SCOPE_ACTION[scope],
            })
    clashes = sorted(email for email, names in names_by_email.items() if len(names) > 1)
    return rows, clashes


def insert_pending(conn, rows: list[dict]) -> int:
    inserted = 0
    for row in rows:
        result = conn.execute(
            """
            INSERT INTO iam.finance_grant (email, display_name, scope, action)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email, scope, display_name) DO NOTHING
            """,
            (row["email"], row["display_name"], row["scope"], row["action"]),
        )
        inserted += result.rowcount or 0
    return inserted
