import types
import json as _json

import pytest

from src.core.context_engine import ContextEngine
from src.web.routes import subscribe_to_updates, demo_publish


def _request_with(engine):
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(context_engine=engine)))


@pytest.mark.asyncio
async def test_subscribe_returns_event_stream(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    resp = await subscribe_to_updates(_request_with(engine), project_id="p", need="database connection settings")

    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"


@pytest.mark.asyncio
async def test_demo_publish_publishes_data(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    result = await demo_publish(
        _request_with(engine),
        project_id="sandbox-demo",
        data_key="db_config",
        data=_json.dumps({"host": "db.internal", "purpose": "primary database connection settings"}),
        data_format="json",
    )

    assert result["status"] == "ok"
    assert result["data_key"] == "db_config"
    keys = await engine.semantic_matcher.get_registered_data("sandbox-demo")
    assert "db_config" in keys
