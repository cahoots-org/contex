"""Sliding-window rate limiting for MCP tools and sandbox routes."""

import math
import os
import time
from collections import defaultdict, deque


def _env_int(name: str, default: int) -> int:
    """Read an integer from the environment, ignoring blanks."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


class RateLimiter:
    """Per-key request counts over the last RATE_LIMIT_WINDOW seconds (default 60).

    ponytail: counts live in this process, so each replica enforces its own
    limit; move them to shared storage if replicas must share one budget.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str, limit_env: str, default_limit: int) -> int | None:
        """Count a request for ``key``; return seconds to wait if over the limit, else None.

        The limit is read from ``limit_env``; a limit <= 0 disables it.
        """
        limit = _env_int(limit_env, default_limit)
        if limit <= 0:
            return None
        window = _env_int("RATE_LIMIT_WINDOW", 60)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, math.ceil(hits[0] + window - now))
        hits.append(now)
        return None
