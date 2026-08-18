# src/web/live.py
"""SSE streaming for the sandbox live demo: a natural-language need backed by an
ephemeral Subscription whose materialized bundle is pushed on every reconcile."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

logger = logging.getLogger(__name__)

DEMO_TOP_K = 10
DEMO_THRESHOLD = 0.3


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def stream_subscription_updates(
    engine, project_id: str, need: str, *, top_k: int = DEMO_TOP_K, threshold: float = DEMO_THRESHOLD,
) -> AsyncIterator[str]:
    """Create an ephemeral subscription for `need`, stream its bundle, and re-stream
    the bundle each time reconcile fires `subscription:{id}:updated`. The subscription
    is always deleted when the stream closes."""
    sub_id = None
    pubsub = None
    try:
        sub_id = await engine.subscriptions.create(project_id, [need], top_k=top_k, threshold=threshold)
        channel = f"subscription:{sub_id}:updated"
        pubsub = engine.redis.pubsub()
        await pubsub.subscribe(channel)

        # Re-read AFTER subscribing so a change racing the create() is not missed.
        bundle = await engine.subscriptions.get_bundle(sub_id)
        yield _sse({"type": "bundle", "bundle": bundle, "updated_at": None})

        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue  # skip subscribe-confirmation / pattern frames
            bundle = await engine.subscriptions.get_bundle(sub_id)
            yield _sse({
                "type": "bundle",
                "bundle": bundle,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
    finally:
        try:
            if pubsub is not None:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
        finally:
            if sub_id is not None:
                await engine.subscriptions.delete(sub_id)
