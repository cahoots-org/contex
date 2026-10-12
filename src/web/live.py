# src/web/live.py
"""SSE streaming for the sandbox live demo: a natural-language need backed by an
ephemeral Subscription whose materialized bundle is pushed on every reconcile."""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator
from uuid import uuid4

import anyio

logger = logging.getLogger(__name__)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def stream_subscription_updates(
    engine, project_id: str, need: str, *, top_k: int = 10, threshold: float = 0.5,
) -> AsyncIterator[str]:
    """Create an ephemeral subscription for `need`, stream its bundle, and re-stream
    it on every update notification. The subscription is always deleted when the
    stream closes."""
    sub_id = None
    queue = None
    try:
        # Private, so deleting it on close can't pull a shared subscription from agents.
        sub_id = await engine.subscriptions.create(
            project_id, [need], top_k=top_k, threshold=threshold, subscription_id=f"sub_{uuid4().hex}",
        )
        queue = engine.notifier.listen(sub_id)

        # Read AFTER listening so a change racing the create() is not missed.
        bundle = await engine.subscriptions.get_bundle(sub_id)
        yield _sse({"type": "bundle", "bundle": bundle})

        # A quiet stream never reads, so renew the lease well before it lapses.
        renew_every = engine.subscriptions.ttl.total_seconds() / 2
        while True:
            with anyio.move_on_after(renew_every) as wait:
                await queue.get()  # an update or RESYNC: either way, re-read
            if wait.cancelled_caught:
                await engine.subscriptions.renew(sub_id)
                continue
            bundle = await engine.subscriptions.get_bundle(sub_id)
            yield _sse({"type": "bundle", "bundle": bundle})
    finally:
        try:
            if queue is not None:
                engine.notifier.unlisten(queue)
        finally:
            if sub_id is not None:
                # Shielded: on disconnect the stream is cancelled, which would abort the delete.
                with anyio.CancelScope(shield=True):
                    await engine.subscriptions.delete(sub_id)
