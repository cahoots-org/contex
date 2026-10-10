"""Sliding time windows (max_age_seconds) and caller-supplied publish times."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server
from src.core.models import DataPublishEvent
from src.core.reconcile_sweep import sweep_once


async def _engine(db):
    engine = ContextEngine(db=db, similarity_threshold=0.0, max_matches=10)
    await engine.initialize()
    return engine


async def _publish(engine, project_id, *keys, **kwargs):
    for key in keys:
        await engine.publish_data(DataPublishEvent(
            project_id=project_id, data_key=key, data={"purpose": "payments api"}, data_format="json",
            **kwargs,
        ))


async def _age(db, data_key, age: timedelta):
    async with db.session() as session:
        await session.execute(
            text("UPDATE embeddings SET created_at = :c, updated_at = NULL WHERE data_key = :k"),
            {"c": datetime.now(timezone.utc) - age, "k": data_key},
        )
        await session.commit()


def _docs(matches):
    return {m["data_key"].split(".", 1)[0] for m in matches}


async def _call(server, tool, args):
    return json.loads((await server.call_tool(tool, args)).content[0].text)


@pytest.mark.asyncio
async def test_query_max_age_filters_old_data(db):
    engine = await _engine(db)
    await _publish(engine, "w", "fresh", "stale")
    await _age(db, "stale", timedelta(hours=2))
    server, _ = build_mcp_server(engine)

    payload = await _call(server, "contex_query", {
        "project_id": "w", "query": "payments api", "top_k": 10, "threshold": 0.0, "max_age_seconds": 3600,
    })
    assert _docs(payload["matches"]) == {"fresh"}


@pytest.mark.asyncio
async def test_non_positive_max_age_is_rejected(db):
    server, _ = build_mcp_server(await _engine(db))
    for tool, args in [
        ("contex_query", {"project_id": "w", "query": "x"}),
        ("contex_create_subscription", {"project_id": "w", "needs": ["x"]}),
    ]:
        with pytest.raises(Exception):
            await server.call_tool(tool, {**args, "max_age_seconds": 0})


@pytest.mark.asyncio
async def test_windowed_subscription_ages_items_out_on_the_sweep(db):
    engine = await _engine(db)
    await _publish(engine, "w", "fresh", "stale")
    await _age(db, "stale", timedelta(hours=2))
    server, _ = build_mcp_server(engine)

    sub = await _call(server, "contex_create_subscription", {
        "project_id": "w", "needs": ["payments api"], "top_k": 10, "threshold": 0.0, "max_age_seconds": 3600,
    })
    sub_id = sub["subscription_id"]
    bundle = await engine.subscriptions.get_bundle(sub_id)
    assert _docs(bundle["payments api"]) == {"fresh"}

    # No publish happens; time passing alone must drop the item.
    await _age(db, "fresh", timedelta(hours=2))
    await sweep_once(db, engine.subscriptions)

    bundle = await engine.subscriptions.get_bundle(sub_id)
    assert bundle["payments api"] == []


@pytest.mark.asyncio
async def test_published_at_backdates_ingested_data(db):
    engine = await _engine(db)
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    await _publish(engine, "b", "backfilled", published_at=long_ago)
    await _publish(engine, "b", "current")
    server, _ = build_mcp_server(engine)

    payload = await _call(server, "contex_query", {
        "project_id": "b", "query": "payments api", "top_k": 10, "threshold": 0.0, "max_age_seconds": 86400,
    })
    assert _docs(payload["matches"]) == {"current"}


@pytest.mark.asyncio
async def test_publish_tools_accept_published_at(db):
    engine = await _engine(db)
    server, _ = build_mcp_server(engine)
    long_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    await server.call_tool("contex_publish", {
        "project_id": "t", "data_key": "one", "data": {"purpose": "payments api"}, "published_at": long_ago,
    })
    await server.call_tool("contex_publish_batch", {"project_id": "t", "items": [
        {"data_key": "two", "data": {"purpose": "payments api"}, "published_at": long_ago},
        {"data_key": "three", "data": {"purpose": "payments api"}},
    ]})

    payload = await _call(server, "contex_query", {
        "project_id": "t", "query": "payments api", "top_k": 10, "threshold": 0.0, "max_age_seconds": 86400,
    })
    assert _docs(payload["matches"]) == {"three"}
