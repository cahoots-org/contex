# src/core/authz.py
"""Auth switch and the route marker for intentionally unauthenticated HTTP routes."""
from __future__ import annotations

import os


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


class _Public:
    """Route dependency marking a route as intentionally unauthenticated."""

    async def __call__(self) -> None:
        return None


public = _Public()
