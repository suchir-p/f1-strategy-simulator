"""Per-client rate limits for the endpoints that start engine work.

A fixed one-minute window per (client, bucket), in memory. That is enough for
a single API process (the deployment this project supports); several API
replicas would need a shared store such as PostgreSQL or Redis.

The client is the connecting address, or the first X-Forwarded-For entry
when Settings.trust_forwarded_for is on (the API sits behind the dashboard's
proxy in docker compose, so every request would otherwise share one address).
"""

from __future__ import annotations

import threading
import time

from fastapi import Depends, HTTPException, Request

from app.api.deps import AppState, state
from app.config import TRACE_RATE_MULTIPLIER

WINDOW_SECONDS = 60.0
MAX_TRACKED = 10_000  # Clients remembered at once; the oldest windows are dropped first.


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}

    def hit(self, client: str, bucket: str, limit: int, now: float | None = None) -> float | None:
        """Counts a request. Returns None if allowed, else seconds until the window resets."""
        now = time.monotonic() if now is None else now
        key = (client, bucket)
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= WINDOW_SECONDS:
                start, count = now, 0
            if count >= limit:
                return WINDOW_SECONDS - (now - start)
            self._windows[key] = (start, count + 1)
            if len(self._windows) > MAX_TRACKED:
                for k in sorted(self._windows, key=lambda k: self._windows[k][0])[:MAX_TRACKED // 10]:
                    del self._windows[k]
            return None


def client_id(request: Request, trust_forwarded_for: bool) -> str:
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def limit(bucket: str):
    """FastAPI dependency: 429 with Retry-After once a client exceeds its budget."""
    def check(request: Request, st: AppState = Depends(state)) -> None:
        per_minute = st.settings.rate_limit_per_minute or 0
        if per_minute <= 0:
            return
        budget = per_minute * (TRACE_RATE_MULTIPLIER if bucket == "traces" else 1)
        wait = st.rate_limiter.hit(client_id(request, st.settings.trust_forwarded_for), bucket, budget)
        if wait is not None:
            raise HTTPException(429, detail=f"too many requests; try again in {int(wait) + 1} s",
                                headers={"Retry-After": str(int(wait) + 1)})
    return check
