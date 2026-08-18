import types

import pytest

from src.core.context_engine import ContextEngine
from src.web.routes import subscribe_to_updates


def _request_with(engine):
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(context_engine=engine)))


@pytest.mark.asyncio
async def test_subscribe_returns_event_stream(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    resp = await subscribe_to_updates(_request_with(engine), project_id="p", need="database connection settings")

    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"
