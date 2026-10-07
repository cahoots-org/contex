import asyncio
import json

import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated
from sqlalchemy import text

from src.core.mcp_bridge import resource_uri_for, run_bridge
from src.core.notifier import SUBSCRIPTION_UPDATED


def test_resource_uri_for():
    assert resource_uri_for("sub_abc") == "contex://subscriptions/sub_abc"


async def _wait_for_uris(seen, expected, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if expected <= {ev.uri for ev in seen if isinstance(ev, ResourceUpdated)}:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {expected}, saw {seen}")


@pytest.mark.asyncio
async def test_bridge_pushes_resource_updated_on_notification(db, notifier):
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))

    async def no_ids():
        return []

    task = asyncio.create_task(run_bridge(notifier, bus, no_ids))
    try:
        await asyncio.sleep(0.05)
        async with db.session() as session:
            await session.execute(
                text("SELECT pg_notify(:c, :p)"),
                {"c": SUBSCRIPTION_UPDATED,
                 "p": json.dumps({"subscription_id": "sub_x", "updated_at": "2026-10-05T00:00:00+00:00"})},
            )
            await session.commit()
        await _wait_for_uris(seen, {"contex://subscriptions/sub_x"})
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_bridge_pushes_every_subscription_on_resync(notifier):
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))

    async def ids():
        return ["sub_a", "sub_b"]

    task = asyncio.create_task(run_bridge(notifier, bus, ids))
    try:
        await asyncio.sleep(0.05)
        notifier._broadcast_resync()
        await _wait_for_uris(seen, {"contex://subscriptions/sub_a", "contex://subscriptions/sub_b"})
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_bridge_stops_listening_when_cancelled(notifier):
    bus = InMemorySubscriptionBus()

    async def no_ids():
        return []

    task = asyncio.create_task(run_bridge(notifier, bus, no_ids))
    await asyncio.sleep(0.05)
    assert len(notifier._all) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert notifier._all == set()
