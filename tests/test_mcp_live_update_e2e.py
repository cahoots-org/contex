# tests/test_mcp_live_update_e2e.py
import asyncio
import json
import pytest
from mcp.server.subscriptions import ResourceUpdated
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.core.mcp_adapter import build_mcp_server
from src.core.mcp_bridge import resource_uri_for, run_bridge


@pytest.mark.asyncio
async def test_publish_pushes_resource_updated_and_bundle_refreshes(db, notifier):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, bus = build_mcp_server(engine)

    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)
    uri = resource_uri_for(sub_id)

    updated = []
    bus.subscribe(lambda ev: updated.append(ev))

    bridge = asyncio.create_task(run_bridge(notifier, bus, engine.subscriptions.all_ids))
    try:
        await asyncio.sleep(0.05)
        await engine.publish_data(DataPublishEvent(
            project_id="p", data_key="db",
            data={"purpose": "database connection settings"}, data_format="json",
        ))
        deadline = asyncio.get_running_loop().time() + 5
        while not any(isinstance(ev, ResourceUpdated) and ev.uri == uri for ev in updated):
            assert asyncio.get_running_loop().time() < deadline, "no resources/updated push"
            await asyncio.sleep(0.05)
    finally:
        bridge.cancel()

    contents = list(await server.read_resource(uri))
    bundle = json.loads(contents[0].content)
    assert any(
        m["data_key"].startswith("db.") or m["data_key"] == "db"
        for matches in bundle.values()
        for m in matches
    )
