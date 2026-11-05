from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from app.settings import Settings, load_settings


def connect(url: str):
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)


def app_connection(settings: Settings | None = None):
    current = settings or load_settings()
    if not current.database_url:
        raise RuntimeError("VGGOS_DATABASE_URL is not set.")
    return connect(current.database_url)
