# tests/test_mcp_live_update_e2e.py
import json
import pytest
from mcp.server.subscriptions import ResourceUpdated
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.core.mcp_adapter import build_mcp_server
from src.core.mcp_bridge import handle_message, resource_uri_for


@pytest.mark.asyncio
async def test_publish_pushes_resource_updated_and_bundle_refreshes(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    server, bus = build_mcp_server(engine)

    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)
    uri = resource_uri_for(sub_id)

    updated = []
    bus.subscribe(lambda ev: updated.append(ev))

    # subscribe to the raw Redis channel to capture the reconcile emit
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"subscription:{sub_id}:updated")

    # publish matching data -> reconcile updates the bundle + emits the redis event
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db",
        data={"purpose": "database connection settings"}, data_format="json",
    ))

    # drain the redis event and drive the bridge (stands in for the running bridge task)
    msg = None
    for _ in range(5):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg:
            break
    assert msg is not None, "reconcile did not emit a subscription-updated event"
    pushed_uri = await handle_message(bus, msg["data"])

    # the MCP resources/updated push fired for our subscription's URI
    assert pushed_uri == uri
    assert any(isinstance(ev, ResourceUpdated) and ev.uri == uri for ev in updated)

    # re-reading the resource shows the freshly-materialized bundle containing the new item
    contents = list(await server.read_resource(uri))
    bundle = json.loads(contents[0].content)
    # Engine decomposes structured JSON into path-suffixed node keys ("db" -> "db.root"/"db.purpose"...)
    assert any(
        m["data_key"].startswith("db.") or m["data_key"] == "db"
        for matches in bundle.values()
        for m in matches
    )
