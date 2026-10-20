import asyncio
import os
import uuid

import pytest

from examples.rumor_mill.contex_client import ContexClient

URL = os.environ.get("RUMOR_MILL_CONTEX_URL")
pytestmark = [
    pytest.mark.skipif(not URL, reason="RUMOR_MILL_CONTEX_URL not set"),
    pytest.mark.asyncio,
]


def project() -> str:
    return f"rumor-mill:test-{uuid.uuid4().hex[:8]}:mind:x"


async def poll_for(client, uri, key, tries=20):
    for _ in range(tries):
        keys = {m.data_key for ms in (await client.read(uri)).values() for m in ms}
        if key in keys:
            return True
        await asyncio.sleep(0.5)
    return False


async def delayed_publish(client, pid):
    await asyncio.sleep(1)
    await client.publish(pid, "u2", {"text": "the baker's oven is cold again"})


async def test_publish_subscribe_read_matches_by_meaning():
    async with ContexClient(URL) as client:
        pid = project()
        uri = await client.subscribe(pid, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        await client.publish(pid, "u1", {"text": "the oven at the baker's shop is cold"})
        assert await poll_for(client, uri, "u1")


async def test_push_arrives_on_publish():
    async with ContexClient(URL) as client:
        pid = project()
        uri = await client.subscribe(pid, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        events = client.listen(uri)
        publish = asyncio.create_task(delayed_publish(client, pid))
        async with asyncio.timeout(30):
            async for _ in events:
                break
        await events.aclose()
        await publish


async def test_mind_projects_are_isolated():
    async with ContexClient(URL) as client:
        a, b = project(), project()
        uri_a = await client.subscribe(a, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        uri_b = await client.subscribe(b, ["anything about my bakery"], max_age_seconds=600, top_k=5)
        await client.publish(a, "u3", {"text": "the baker's oven is cold"})
        assert await poll_for(client, uri_a, "u3")
        assert not await poll_for(client, uri_b, "u3", tries=3)
