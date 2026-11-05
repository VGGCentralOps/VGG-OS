"""In-memory limits for the sign-in route. Keys are not written to the log."""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, limit: int = 10, window_seconds: int = 900):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True
