"""Bridges subscription-updated notifications into MCP resources/updated pushes.
The second of two modules allowed to import the mcp SDK."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from mcp.server.subscriptions import ResourceUpdated

from src.core.notifier import RESYNC, Notifier

logger = logging.getLogger(__name__)

_RESOURCE_URI_PREFIX = "contex://subscriptions/"


def resource_uri_for(subscription_id: str) -> str:
    return f"{_RESOURCE_URI_PREFIX}{subscription_id}"


def subscription_id_for(uri: str) -> str:
    """The subscription id in a resource URI (the inverse of resource_uri_for)."""
    return uri.removeprefix(_RESOURCE_URI_PREFIX)


async def run_bridge(
    notifier: Notifier, bus, subscription_ids: Callable[[], Awaitable[list[str]]]
) -> None:
    """Push resources/updated for each update; after RESYNC, for every subscription.
    Runs until cancelled."""
    queue = notifier.listen()
    try:
        while True:
            item = await queue.get()
            try:
                ids = await subscription_ids() if item == RESYNC else [item]
                for subscription_id in ids:
                    await bus.publish(ResourceUpdated(uri=resource_uri_for(subscription_id)))
            except Exception:
                logger.exception("MCP bridge failed to push an update; continuing")
    finally:
        notifier.unlisten(queue)
