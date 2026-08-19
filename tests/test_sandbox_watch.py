# tests/test_sandbox_watch.py
import types
from pathlib import Path

import pytest

from src.core.context_engine import ContextEngine
from src.web.routes import sandbox_home, subscribe_to_updates


def _request_with(engine):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(context_engine=engine))
    )


@pytest.mark.asyncio
async def test_sandbox_home_renders(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    resp = await sandbox_home(_request_with(engine))
    assert resp.template.name == "sandbox.html"


@pytest.mark.asyncio
async def test_subscribe_returns_event_stream(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    resp = await subscribe_to_updates(_request_with(engine), project_id="p", need="database connection settings")
    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"


def test_sandbox_template_has_watch_and_query_wiring():
    html = Path("src/web/templates/sandbox.html").read_text()
    # Live mode wiring:
    assert "EventSource" in html
    assert "/sandbox/subscribe" in html
    assert "startWatch" in html
    # Test mode (one-shot query) still present:
    assert "/sandbox/query" in html
