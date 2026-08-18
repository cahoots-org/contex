import asyncio
import json
import logging
import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated
from src.core.mcp_bridge import handle_message, resource_uri_for, run_bridge


@pytest.mark.asyncio
async def test_handle_message_publishes_resource_updated():
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))
    payload = json.dumps({"subscription_id": "sub_abc", "updated_at": "2026-08-18T00:00:00Z"})

    uri = await handle_message(bus, payload)

    assert uri == "contex://subscriptions/sub_abc"
    assert resource_uri_for("sub_abc") == uri
    assert any(isinstance(ev, ResourceUpdated) and ev.uri == uri for ev in seen)


@pytest.mark.asyncio
async def test_handle_message_ignores_garbage():
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))
    assert await handle_message(bus, b"not json") is None
    assert seen == []


@pytest.mark.asyncio
async def test_run_bridge_pushes_on_real_redis_event(redis):
    """End-to-end: run_bridge psubscribes, receives a Redis publish, and fans
    out a ResourceUpdated to the bus — exercising the full loop."""
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))

    stop_event = asyncio.Event()
    task = asyncio.create_task(run_bridge(redis, bus, stop_event))

    # Give the bridge a moment to psubscribe before publishing.
    await asyncio.sleep(0.1)

    payload = json.dumps({"subscription_id": "sub_x", "updated_at": "2026-08-18T00:00:00Z"})
    await redis.publish("subscription:sub_x:updated", payload.encode())

    # Poll (up to ~2s) until a ResourceUpdated with the expected URI appears.
    expected_uri = "contex://subscriptions/sub_x"
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        if any(isinstance(ev, ResourceUpdated) and ev.uri == expected_uri for ev in seen):
            break
        await asyncio.sleep(0.05)

    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.TimeoutError:
        task.cancel()

    assert any(isinstance(ev, ResourceUpdated) and ev.uri == expected_uri for ev in seen), (
        f"Expected ResourceUpdated(uri={expected_uri!r}) but saw: {seen}"
    )


@pytest.mark.asyncio
async def test_handle_message_logs_on_garbage(caplog):
    """Malformed messages must return None AND emit a WARNING (Fix A)."""
    bus = InMemorySubscriptionBus()
    with caplog.at_level(logging.WARNING):
        result = await handle_message(bus, b"not json")
    assert result is None
    assert "malformed" in caplog.text.lower()
