"""Idempotent seed data for the sandbox live demo."""
from __future__ import annotations

from src.core.models import DataPublishEvent

DEMO_PROJECT_ID = "sandbox-demo"
DEMO_NEED = "database connection settings"

# (data_key, data) — each item's "purpose" is what the semantic need matches against.
DEMO_ITEMS: list[tuple[str, dict]] = [
    ("db_config", {
        "host": "localhost", "port": 5432, "database": "app",
        "purpose": "primary database connection settings",
    }),
    ("api_config", {
        "base_url": "https://api.example.com", "timeout_seconds": 30,
        "purpose": "external API client configuration",
    }),
    ("cache_config", {
        "backend": "redis", "ttl_seconds": 300,
        "purpose": "cache layer settings",
    }),
]


async def ensure_demo_seed(engine) -> None:
    """Publish the demo project's data if it isn't already present. Safe to call repeatedly."""
    existing = await engine.semantic_matcher.get_registered_data(DEMO_PROJECT_ID)
    if existing:
        return
    for data_key, data in DEMO_ITEMS:
        await engine.publish_data(DataPublishEvent(
            project_id=DEMO_PROJECT_ID, data_key=data_key, data=data, data_format="json",
        ))
