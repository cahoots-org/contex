# tests/test_sandbox_watch.py
import types
from pathlib import Path

import pytest

from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.web.routes import project_stats, sandbox_home, subscribe_to_updates


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


@pytest.mark.asyncio
async def test_project_stats_renders_from_embeddings(db, redis):
    """Regression for #107: the stats handler must read from the embeddings
    table (Postgres), not dead RediSearch attrs, and return 200-worthy stats."""
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    await engine.publish_data(DataPublishEvent(
        project_id="stats-proj", data_key="db",
        data={"purpose": "database connection settings"}, data_format="json",
        description="DB config",
    ))
    await engine.publish_data(DataPublishEvent(
        project_id="stats-proj", data_key="auth",
        data={"purpose": "auth token secret"}, data_format="json",
        description="Auth config",
    ))

    resp = await project_stats(_request_with(engine), project_id="stats-proj")

    assert resp.template.name == "project_stats.html"
    ctx = resp.context
    assert ctx["project_id"] == "stats-proj"
    # Two distinct data_keys published.
    assert ctx["data_count"] == 2
    assert ctx["total_tokens"] > 0
    keys = {item["data_key"] for item in ctx["data_items"]}
    assert keys == {"db", "auth"}
    # Every item carries a non-negative token count and a description field.
    for item in ctx["data_items"]:
        assert item["token_count"] >= 0
        assert "description" in item
    # Sorted by token_count descending.
    counts = [item["token_count"] for item in ctx["data_items"]]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.asyncio
async def test_project_stats_empty_project(db, redis):
    """An unknown project should render cleanly with zeroed stats, not 500."""
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    resp = await project_stats(_request_with(engine), project_id="no-such-project")

    assert resp.template.name == "project_stats.html"
    assert resp.context["data_count"] == 0
    assert resp.context["total_tokens"] == 0
    assert resp.context["data_items"] == []


def test_sandbox_template_has_watch_and_query_wiring():
    html = Path("src/web/templates/sandbox.html").read_text()
    # Live mode wiring:
    assert "EventSource" in html
    assert "/sandbox/subscribe" in html
    assert "startWatch" in html
    # Test mode (one-shot query) still present:
    assert "/sandbox/query" in html
