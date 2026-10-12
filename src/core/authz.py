# src/core/authz.py
"""Whether API-key auth is on."""
from __future__ import annotations

import os


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"

