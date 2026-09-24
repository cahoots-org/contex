"""Streaming body-size limits for uploads and imports (issue #61).

Request bodies must never be fully buffered before the size cap is enforced.
These helpers read a body in bounded chunks with a running total and abort as
soon as the limit is exceeded. A global ASGI middleware bounds any request as
defense-in-depth, since a spoofed ``Content-Length`` cannot be trusted.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from fastapi import Request, UploadFile

DEFAULT_MAX_UPLOAD_SIZE = 50 * 1024 * 1024

UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


class RequestBodyTooLarge(Exception):
    """Raised when a request body exceeds the configured maximum size."""

    def __init__(self, max_size: int):
        self.max_size = max_size
        super().__init__(f"Request body exceeds maximum size of {max_size} bytes")


def get_max_upload_size() -> int:
    """Resolve the effective upload cap from ``CONTEX_MAX_UPLOAD_SIZE``.

    Falls back to the 50 MB default when the variable is unset, non-numeric, or
    non-positive. Read at call time so overrides take effect without reimport.
    """
    try:
        value = int(os.getenv("CONTEX_MAX_UPLOAD_SIZE", str(DEFAULT_MAX_UPLOAD_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_UPLOAD_SIZE
    return value if value > 0 else DEFAULT_MAX_UPLOAD_SIZE


async def stream_upload_file(file: "UploadFile", max_size: int) -> bytes:
    """Read an ``UploadFile`` in bounded chunks, aborting once ``max_size`` is
    exceeded. Never buffers more than the running body plus one chunk."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise RequestBodyTooLarge(max_size)
        chunks.append(chunk)
    return b"".join(chunks)


async def stream_request_body(request: "Request", max_size: int) -> bytes:
    """Read a raw request body in bounded chunks, aborting once ``max_size`` is
    exceeded. Rejects an over-limit ``Content-Length`` before consuming the
    stream; the streamed running total remains the real guard."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_size:
                raise RequestBodyTooLarge(max_size)
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_size:
            raise RequestBodyTooLarge(max_size)
        chunks.append(chunk)
    return b"".join(chunks)


class BodyLimitMiddleware:
    """ASGI middleware that bounds every request body as defense-in-depth.

    Rejects an over-limit ``Content-Length`` up front, then counts streamed
    bytes so a spoofed header cannot slip a larger body past the cap. On
    violation it responds ``413`` and stops forwarding the body downstream.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_size = get_max_upload_size()

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > max_size:
                        await self._reject(send)
                        return
                except ValueError:
                    pass
                break

        total = 0
        response_started = False

        async def _receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > max_size:
                    raise RequestBodyTooLarge(max_size)
            return message

        async def _send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, _receive, _send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"Request body too large"}',
        })
