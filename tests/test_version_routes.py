"""
Integration tests for the data-versioning API (src/api/version_routes.py).

Regression coverage for issue #106: the routes previously called the
non-existent ``EventStore.get_events()`` and 500'd on every request. These
tests drive all four endpoints (history / version / diff / restore) against a
real ContextEngine backed by the live Postgres event store and assert they
return 200 for a normal case.
"""

import numpy as np
import pytest
import pytest_asyncio
from unittest.mock import Mock, patch

from fastapi import FastAPI
from httpx import AsyncClient

from src.api.version_routes import router as version_router
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest_asyncio.fixture
async def engine(db, redis):
    """Real ContextEngine on the live test DB, with the heavy embedding model mocked."""
    with patch("src.core.semantic_matcher.SentenceTransformer") as mock_model_cls:
        mock_model = Mock()
        mock_model.encode.return_value = np.array([0.1] * 384, dtype=np.float32)
        mock_model_cls.return_value = mock_model

        engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.5, max_matches=10)
        if hasattr(engine.semantic_matcher, "initialize_index"):
            await engine.semantic_matcher.initialize_index()
        yield engine


@pytest_asyncio.fixture
async def client(engine):
    """FastAPI app mounting only the versioning router, wired to the real engine."""
    app = FastAPI()
    app.include_router(version_router)
    app.state.context_engine = engine
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c


async def _publish(engine, project_id, data_key, data):
    return await engine.publish_data(
        DataPublishEvent(project_id=project_id, data_key=data_key, data=data)
    )


@pytest.mark.asyncio
async def test_history_returns_200_with_versions(engine, client):
    project_id = "ver-proj"
    data_key = "tech_stack"
    await _publish(engine, project_id, data_key, {"db": "postgres"})
    await _publish(engine, project_id, data_key, {"db": "postgres", "cache": "redis"})
    # An unrelated key must not leak into this key's history.
    await _publish(engine, project_id, "other_key", {"unrelated": True})

    resp = await client.get(f"/api/v1/versions/projects/{project_id}/data/{data_key}/history")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2
    assert len(body["versions"]) == 2
    # Most-recent first.
    assert body["versions"][0]["data"] == {"db": "postgres", "cache": "redis"}
    assert body["versions"][1]["data"] == {"db": "postgres"}


@pytest.mark.asyncio
async def test_history_empty_key_returns_200(client):
    resp = await client.get(
        "/api/v1/versions/projects/ver-proj/data/does_not_exist/history"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 0
    assert body["versions"] == []


@pytest.mark.asyncio
async def test_specific_version_returns_200(engine, client):
    project_id = "ver-proj"
    data_key = "config"
    seq = await _publish(engine, project_id, data_key, {"level": "info"})

    resp = await client.get(
        f"/api/v1/versions/projects/{project_id}/data/{data_key}/version/{seq}"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sequence"] == seq
    assert body["data"] == {"level": "info"}


@pytest.mark.asyncio
async def test_diff_returns_200(engine, client):
    project_id = "ver-proj"
    data_key = "config"
    seq1 = await _publish(engine, project_id, data_key, {"level": "info"})
    seq2 = await _publish(engine, project_id, data_key, {"level": "debug"})

    resp = await client.get(
        f"/api/v1/versions/projects/{project_id}/data/{data_key}/diff",
        params={"from_sequence": seq1, "to_sequence": seq2},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_data"] == {"level": "info"}
    assert body["to_data"] == {"level": "debug"}
    assert body["changed"] is True


@pytest.mark.asyncio
async def test_restore_returns_200_and_appends_event(engine, client):
    project_id = "ver-proj"
    data_key = "config"
    seq1 = await _publish(engine, project_id, data_key, {"level": "info"})
    await _publish(engine, project_id, data_key, {"level": "debug"})

    resp = await client.post(
        f"/api/v1/versions/projects/{project_id}/data/{data_key}/restore/{seq1}"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["restored_from"] == seq1
    assert body["data"] == {"level": "info"}
    # Restoration appends a brand-new event whose sequence is beyond the originals.
    assert int(body["new_sequence"]) > int(seq1)

    # The restored value is now the newest version in history.
    hist = await client.get(
        f"/api/v1/versions/projects/{project_id}/data/{data_key}/history"
    )
    assert hist.status_code == 200
    assert hist.json()["versions"][0]["data"] == {"level": "info"}
