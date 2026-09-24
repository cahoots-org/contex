"""Tests for streaming upload size limits and the ASGI body-limit middleware.

Covers issue #61: the upload/import paths must never buffer the whole body
before enforcing the size cap, and a global ASGI middleware must bound any
request as defense-in-depth.
"""

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Request
from httpx import AsyncClient

from src.core.upload_limits import (
    BodyLimitMiddleware,
    RequestBodyTooLarge,
    UPLOAD_CHUNK_SIZE,
    get_max_upload_size,
    stream_request_body,
    stream_upload_file,
)


class _FakeUploadFile:
    """Minimal UploadFile stand-in that yields fixed-size chunks."""

    def __init__(self, total: int, chunk: int):
        self._remaining = total
        self._chunk = chunk
        self.reads = []

    async def read(self, size: int = -1) -> bytes:
        n = self._chunk if size < 0 else min(size, self._chunk)
        n = min(n, self._remaining)
        self.reads.append(size)
        self._remaining -= n
        return b"x" * n


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

    def test_features_config_reads_env(self, monkeypatch):
        monkeypatch.setenv("CONTEX_MAX_UPLOAD_SIZE", "2048")
        from src.core.config import ContexConfig

        config = ContexConfig.from_env()
        assert config.features.max_upload_size == 2048


class TestStreamUploadFile:
    @pytest.mark.asyncio
    async def test_reads_in_bounded_chunks_without_full_buffer(self):
        upload = _FakeUploadFile(total=UPLOAD_CHUNK_SIZE * 3, chunk=UPLOAD_CHUNK_SIZE)
        content = await stream_upload_file(upload, max_size=UPLOAD_CHUNK_SIZE * 4)
        assert len(content) == UPLOAD_CHUNK_SIZE * 3
        # Never requested more than one chunk at a time.
        assert all(size == UPLOAD_CHUNK_SIZE for size in upload.reads)
        assert len(upload.reads) >= 3

    @pytest.mark.asyncio
    async def test_aborts_mid_transfer_when_limit_exceeded(self):
        upload = _FakeUploadFile(total=UPLOAD_CHUNK_SIZE * 10, chunk=UPLOAD_CHUNK_SIZE)
        with pytest.raises(RequestBodyTooLarge):
            await stream_upload_file(upload, max_size=UPLOAD_CHUNK_SIZE * 2)
        # Aborted early: did not read the whole payload.
        assert len(upload.reads) <= 4


class TestStreamRequestBody:
    @pytest.mark.asyncio
    async def test_content_length_over_limit_rejected_before_reading(self):
        async def _fail_stream():
            raise AssertionError("stream() must not be consumed when Content-Length exceeds limit")
            yield b""  # pragma: no cover

        request = MagicMock(spec=Request)
        request.headers = {"content-length": str(1000)}
        request.stream = _fail_stream
        with pytest.raises(RequestBodyTooLarge):
            await stream_request_body(request, max_size=10)

    @pytest.mark.asyncio
    async def test_missing_content_length_still_streamed_and_bounded(self):
        chunks = [b"a" * 8, b"b" * 8, b"c" * 8]

        async def _stream():
            for c in chunks:
                yield c

        request = MagicMock(spec=Request)
        request.headers = {}
        request.stream = _stream
        with pytest.raises(RequestBodyTooLarge):
            await stream_request_body(request, max_size=10)

    @pytest.mark.asyncio
    async def test_within_limit_returns_full_body(self):
        async def _stream():
            yield b"hello "
            yield b"world"

        request = MagicMock(spec=Request)
        request.headers = {}
        request.stream = _stream
        body = await stream_request_body(request, max_size=1000)
        assert body == b"hello world"


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
