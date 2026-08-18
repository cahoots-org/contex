"""Bridges Plan A's Redis `subscription:{id}:updated` events into MCP resources/updated
notifications. The second of two modules allowed to import the mcp SDK."""
from __future__ import annotations

import asyncio
import json
import logging

from mcp.server.subscriptions import ResourceUpdated

logger = logging.getLogger(__name__)

_CHANNEL_PATTERN = "subscription:*:updated"


def resource_uri_for(subscription_id: str) -> str:
    return f"contex://subscriptions/{subscription_id}"


async def handle_message(bus, raw) -> str | None:
    try:
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        sub_id = data["subscription_id"]
    except (ValueError, KeyError, AttributeError, TypeError):
        return None
    uri = resource_uri_for(sub_id)
    await bus.publish(ResourceUpdated(uri=uri))
    return uri


async def run_bridge(redis, bus, stop_event: asyncio.Event) -> None:
    pubsub = redis.pubsub()
    await pubsub.psubscribe(_CHANNEL_PATTERN)
    logger.info("MCP bridge listening on %s", _CHANNEL_PATTERN)
    try:
        while not stop_event.is_set():
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            try:
                if msg and msg.get("type") in ("pmessage", "message"):
                    await handle_message(bus, msg["data"])
            except Exception:
                logger.exception("MCP bridge loop error; continuing")
    finally:
        await pubsub.close()
