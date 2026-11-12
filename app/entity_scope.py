"""Persist and resolve the active company (entity) for the product shell.

Companies come from core.entity rows bound to source.company. Switching is a
read-scope concern: screens filter by the chosen code. Pulling more companies
later does not require a UI rewrite — only BC env/base URL changes.
"""

from __future__ import annotations

from app.constants import ADVANCLY_ENTITY_CODE

ENTITY_COOKIE = "vggos_entity"
ENTITY_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def resolve_entity(request, entities: list[dict]) -> dict | None:
    """Pick the active entity from ?entity=, cookie, Advancly default, then first."""
    if not entities:
        return None
    by_code = {row["code"]: row for row in entities}
    query = (request.query_params.get("entity") or "").strip()
    cookie = (request.cookies.get(ENTITY_COOKIE) or "").strip()
    for candidate in (query, cookie, ADVANCLY_ENTITY_CODE):
        if candidate and candidate in by_code:
            return by_code[candidate]
    return entities[0]


def set_entity_cookie(response, entity_code: str, *, secure: bool) -> None:
    response.set_cookie(
        ENTITY_COOKIE,
        entity_code,
        max_age=ENTITY_COOKIE_MAX_AGE,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
