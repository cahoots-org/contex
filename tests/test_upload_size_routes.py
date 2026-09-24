"""Route-level tests for streaming upload/import size limits (issue #61).

Exercises the /data/upload and /projects/{id}/import handlers to confirm they
reject oversized bodies with 413 via the streaming reader, honour a
Content-Length precheck and the env-configurable cap, and never buffer the
whole payload.
"""

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request
from httpx import AsyncClient

from src.api.routes import router as api_router


def _chainable_histogram():
    mock = MagicMock()
    mock.labels.return_value = MagicMock()
    mock.labels.return_value.observe = MagicMock()
    return mock


def _build_app():
    app = FastAPI()

    @app.middleware("http")
    async def _seed_principal(request: Request, call_next):
        request.state.api_key_id = "test-key"
        request.state.tenant_id = "t1"
        request.state.request_id = "req-1"
        return await call_next(request)

    app.include_router(api_router, prefix="/api/v1")

    mock_engine = MagicMock()
    mock_engine.publish_data = AsyncMock(return_value="42")
    app.state.context_engine = mock_engine
    app.state.db = MagicMock()
    return app, mock_engine


@pytest.mark.asyncio
async def test_upload_oversized_rejected_via_streaming(monkeypatch):
    monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "1024")
    app, mock_engine = _build_app()

    with (
        patch("src.api.routes.ensure_project_access", new=AsyncMock()),
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.core.metrics.record_event_published", new=MagicMock()),
        patch("src.core.metrics.publish_duration_seconds", new=_chainable_histogram()),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/data/upload",
                data={"project_id": "p", "data_key": "k"},
                files={"file": ("big.txt", b"x" * 4096, "text/plain")},
            )

    assert resp.status_code == 413, resp.text
    mock_engine.publish_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_env_override_changes_effective_limit(monkeypatch):
    monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", str(4 * 1024 * 1024))
    app, mock_engine = _build_app()

    with (
        patch("src.api.routes.ensure_project_access", new=AsyncMock()),
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.core.metrics.record_event_published", new=MagicMock()),
        patch("src.core.metrics.publish_duration_seconds", new=_chainable_histogram()),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/data/upload",
                data={"project_id": "p", "data_key": "k"},
                files={"file": ("ok.txt", b"y" * 8192, "text/plain")},
            )

    assert resp.status_code == 200, resp.text
    mock_engine.publish_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_streams_in_chunks_not_full_buffer(monkeypatch):
    """The handler must read the UploadFile via stream_upload_file, not file.read()."""
    monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", str(4 * 1024 * 1024))
    app, mock_engine = _build_app()

    called = {"stream": False}
    real = __import__("src.core.upload_limits", fromlist=["stream_upload_file"]).stream_upload_file

    async def _tracking_stream(file, max_size):
        called["stream"] = True
        return await real(file, max_size)

    with (
        patch("src.api.routes.ensure_project_access", new=AsyncMock()),
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.api.routes.stream_upload_file", new=_tracking_stream),
        patch("src.core.metrics.record_event_published", new=MagicMock()),
        patch("src.core.metrics.publish_duration_seconds", new=_chainable_histogram()),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/data/upload",
                data={"project_id": "p", "data_key": "k"},
                files={"file": ("ok.txt", b"z" * 16, "text/plain")},
            )

    assert resp.status_code == 200, resp.text
    assert called["stream"] is True


@pytest.mark.asyncio
async def test_import_oversized_rejected(monkeypatch):
    monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "1024")
    app, _ = _build_app()

    with (
        patch("src.api.routes.ensure_project_access", new=AsyncMock()),
        patch("src.api.routes.audit_log", new=AsyncMock()),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/projects/p/import",
                content=b"{" + b"x" * 4096 + b"}",
                headers={"content-type": "application/json"},
            )

    assert resp.status_code == 413, resp.text


@pytest.mark.asyncio
async def test_import_within_limit_reaches_manager(monkeypatch):
    monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", str(4 * 1024 * 1024))
    app, _ = _build_app()

    mock_manager = MagicMock()
    mock_manager.import_project = AsyncMock(return_value={"status": "success", "stats": {"project_id": "p"}})

    with (
        patch("src.api.routes.ensure_project_access", new=AsyncMock()),
        patch("src.api.routes.audit_log", new=AsyncMock()),
        patch("src.core.export_import.ExportImportManager", return_value=mock_manager),
    ):
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/projects/p/import",
                content=b'{"project_id": "p"}',
                headers={"content-type": "application/json"},
            )

    assert resp.status_code == 200, resp.text
    mock_manager.import_project.assert_awaited_once()
