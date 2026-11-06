from __future__ import annotations

import threading

import psycopg
from psycopg.rows import dict_row

from app.settings import Settings, load_settings

_POOLS: dict[str, "Pool"] = {}
_POOLS_GUARD = threading.Lock()


def connect(url: str):
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=10)


class Pool:
    """A few open database connections, reused across page loads."""

    def __init__(self, url: str, size: int = 4):
        self.url = url
        self.size = size
        self._idle: list = []
        self._count = 0
        self._lock = threading.Lock()

    def release(self, conn, *, broken: bool = False) -> None:
        with self._lock:
            if broken or getattr(conn, "closed", True):
                self._count = max(0, self._count - 1)
                return
            if len(self._idle) < self.size:
                self._idle.append(conn)
                return
            self._count = max(0, self._count - 1)
        conn.close()

    def take(self):
        with self._lock:
            while self._idle:
                conn = self._idle.pop()
                if not conn.closed:
                    return conn
                self._count = max(0, self._count - 1)
            self._count += 1
        try:
            return connect(self.url)
        except Exception:
            with self._lock:
                self._count = max(0, self._count - 1)
            raise

    def connection(self):
        return _Lease(self)


class _Lease:
    def __init__(self, pool: Pool):
        self.pool = pool
        self.conn = None

    def __enter__(self):
        self.conn = self.pool.take()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        broken = exc_type is not None
        try:
            if self.conn is not None and not self.conn.closed:
                if exc_type is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
        except Exception:
            broken = True
        if self.conn is not None:
            self.pool.release(self.conn, broken=broken)
        return False


def pool_for(url: str) -> Pool:
    with _POOLS_GUARD:
        pool = _POOLS.get(url)
        if pool is None:
            pool = Pool(url)
            _POOLS[url] = pool
            warm = True
        else:
            warm = False
    if warm:
        try:
            with pool.connection() as conn:
                conn.execute("SELECT 1")
        except Exception:
            pass
    return pool


def app_connection(settings: Settings | None = None):
    current = settings or load_settings()
    if not current.database_url:
        raise RuntimeError("VGGOS_DATABASE_URL is not set.")
    return pool_for(current.database_url).connection()
