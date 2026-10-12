# tests/test_sandbox_watch.py
import html
import json
import re
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent
from src.core.rate_limiter import RateLimiter
from src.web import routes
from src.web.routes import execute_query, sandbox_home, subscribe_to_updates


def _request_with(engine, db=None):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(context_engine=engine, db=db))
    )


@pytest.mark.asyncio
async def test_sandbox_home_renders(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    resp = await sandbox_home(_request_with(engine, db))
    assert resp.template.name == "sandbox.html"


@pytest.mark.asyncio
async def test_sandbox_home_passes_project_id_to_script_as_a_string(db):
    project_id = "!',x:alert(document.domain),y:'"
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    await engine.publish_data(DataPublishEvent(project_id=project_id, data_key="k", data={"a": 1}))

    resp = await sandbox_home(_request_with(engine, db))

    expression = re.search(r"selectedProject: (.*),\n", html.unescape(resp.body.decode())).group(1)
    assert json.loads(expression) == project_id


@pytest.mark.asyncio
async def test_subscribe_returns_event_stream(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    resp = await subscribe_to_updates(
        _request_with(engine, db),
        project_id="p",
        need="database connection settings",
    )
    assert resp.media_type == "text/event-stream"
    assert resp.headers["Cache-Control"] == "no-cache"


@pytest.mark.asyncio
async def test_query_renders_matches_as_json(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    await engine.publish_data(DataPublishEvent(
        project_id="q-proj", data_key="db", data={"purpose": "database connection settings"},
    ))

    resp = await execute_query(
        _request_with(engine), project_id="q-proj", query="database connection",
        top_k=5, threshold=0.0,
    )

    assert resp.status_code == 200
    assert "database connection settings" in resp.body.decode()


@pytest.mark.asyncio
async def test_query_is_rate_limited(db, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_QUERY", "1")
    monkeypatch.setattr(routes, "_limiter", RateLimiter())
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)

    async def query():
        return await execute_query(
            _request_with(engine), project_id="p", query="anything", top_k=5, threshold=0.5,
        )

    await query()
    with pytest.raises(HTTPException) as limited:
        await query()
    assert limited.value.status_code == 429


def test_sandbox_template_has_watch_and_query_wiring():
    html = Path("src/web/templates/sandbox.html").read_text()
    # Live mode wiring:
    assert "EventSource" in html
    assert "/sandbox/subscribe" in html
    assert "startWatch" in html
    # Test mode (one-shot query) still present:
    assert "/sandbox/query" in html
