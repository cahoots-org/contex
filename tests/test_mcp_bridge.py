import json
import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated
from src.core.mcp_bridge import handle_message, resource_uri_for


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
    assert await handle_message(bus, b"not json") is None
