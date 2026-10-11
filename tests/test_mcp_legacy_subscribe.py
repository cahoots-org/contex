"""2025-era clients subscribe with resources/subscribe and still get push (#264)."""
import asyncio
import json
import warnings

import pytest
from mcp import Client, types
from mcp.server.subscriptions import ResourceUpdated
from mcp.shared.exceptions import MCPError

from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server

warnings.filterwarnings("ignore", message="resources/(un)?subscribe is removed")


async def _setup(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    return build_mcp_server(engine)


def _recorder():
    seen: asyncio.Queue[str] = asyncio.Queue()

    async def on_message(message):
        if isinstance(message, types.ResourceUpdatedNotification):
            await seen.put(message.params.uri)
    return seen, on_message


async def _subscription_uri(client) -> str:
    result = await client.call_tool("contex_create_subscription", {"project_id": "p", "needs": ["payments"]})
    return json.loads(result.content[0].text)["resource_uri"]


@pytest.mark.asyncio
async def test_legacy_subscriber_is_pushed_updates(db):
    server, bus = await _setup(db)
    seen, on_message = _recorder()
    async with Client(server, mode="legacy", message_handler=on_message) as client:
        uri = await _subscription_uri(client)
        await client.subscribe_resource(uri)

        await bus.publish(ResourceUpdated(uri=uri))
        assert await asyncio.wait_for(seen.get(), timeout=5) == uri


@pytest.mark.asyncio
async def test_unsubscribed_client_is_not_pushed(db):
    server, bus = await _setup(db)
    seen, on_message = _recorder()
    async with Client(server, mode="legacy", message_handler=on_message) as client:
        uri = await _subscription_uri(client)
        await client.subscribe_resource(uri)
        await client.unsubscribe_resource(uri)

        await bus.publish(ResourceUpdated(uri=uri))
        await asyncio.sleep(0.2)
        assert seen.empty()


@pytest.mark.asyncio
async def test_unknown_subscription_cannot_be_subscribed(db):
    server, _ = await _setup(db)
    async with Client(server, mode="legacy") as client:
        with pytest.raises(MCPError):
            await client.subscribe_resource("contex://subscriptions/sub_missing")
