"""Tests for the upload size cap and the ASGI body-limit middleware."""

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient

from src.core.upload_limits import BodyLimitMiddleware, get_max_upload_size


class TestConfigOverride:
    def test_default_matches_50mb(self, monkeypatch):
        monkeypatch.delenv("CONTEX_MAX_UPLOAD_SIZE", raising=False)
        assert get_max_upload_size() == 50 * 1024 * 1024

    def test_env_override_changes_effective_limit(self, monkeypatch):
        monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "1024")
        assert get_max_upload_size() == 1024

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "not-a-number")
        assert get_max_upload_size() == 50 * 1024 * 1024


class TestBodyLimitMiddleware:
    @pytest.mark.asyncio
    async def test_bounds_non_upload_route(self, monkeypatch):
        monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "16")
        app = FastAPI()
        app.add_middleware(BodyLimitMiddleware)

        @app.post("/echo")
        async def _echo(request: Request):
            body = await request.body()
            return {"len": len(body)}

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            over = await client.post("/echo", content=b"x" * 64)
            assert over.status_code == 413

            ok = await client.post("/echo", content=b"x" * 8)
            assert ok.status_code == 200
            assert ok.json()["len"] == 8

    @pytest.mark.asyncio
    async def test_rejects_spoofed_content_length(self, monkeypatch):
        monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "16")
        app = FastAPI()
        app.add_middleware(BodyLimitMiddleware)

        @app.post("/echo")
        async def _echo(request: Request):
            body = await request.body()
            return {"len": len(body)}

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/echo",
                content=b"x" * 64,
                headers={"content-length": "8"},
            )
            assert resp.status_code == 413
