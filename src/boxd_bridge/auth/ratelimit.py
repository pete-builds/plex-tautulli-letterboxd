"""In-memory sliding-window rate limiter, keyed by client IP.

Applied to PIN creation so this instance cannot be used to spam plex.tv.
In-memory is deliberate: the app has no datastore, and a single container does
not need shared limiter state.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        cutoff = moment - self._window
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(moment)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
