"""Tests for the global exception handler that sanitizes 500 responses.

Unexpected exceptions must return a generic body with no internal detail
(no SQLAlchemy query text, schema names, or exception message), while
carrying an X-Trace-Id header for server-side correlation. Intentional
4xx HTTPExceptions must still surface their detail.
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.error_handlers import register_exception_handlers
from src.core.tracing import initialize_tracing
from src.core.tracing_middleware import TracingMiddleware


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    manager = initialize_tracing(service_name="test", service_version="0.0.0")
    manager.instrument_fastapi(app)
    app.add_middleware(TracingMiddleware)

    @app.get("/boom")
    async def boom():
        raise RuntimeError(
            "SELECT embeddings.vector FROM embeddings WHERE tenant_id = 'acme'"
        )

    @app.get("/missing")
    async def missing():
        raise HTTPException(status_code=404, detail="Widget not found")

    return app


def test_unexpected_exception_returns_generic_500():
    client = TestClient(_build_app(), raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    body = response.text
    assert "SELECT" not in body
    assert "embeddings" not in body
    assert "tenant_id" not in body


def test_500_includes_trace_id_header():
    client = TestClient(_build_app(), raise_server_exceptions=False)

    response = client.get("/boom")

    assert response.status_code == 500
    assert response.headers.get("X-Trace-Id")


def test_server_log_records_real_error(caplog):
    client = TestClient(_build_app(), raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        client.get("/boom")

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records
    logged = "\n".join(r.getMessage() + str(getattr(r, "extra_fields", "")) for r in error_records)
    assert "RuntimeError" in logged or "SELECT" in logged


def test_intentional_4xx_detail_preserved():
    client = TestClient(_build_app(), raise_server_exceptions=False)

    response = client.get("/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Widget not found"}
