"""Tests for Prometheus metrics"""

import pytest
from fastapi import APIRouter, FastAPI
from mcp.server.mcpserver.exceptions import ToolError
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from src.core.metrics import (
    http_requests_total,
    active_requests,
    record_http_request,
    increment_active_requests,
    decrement_active_requests,
    get_metrics,
    mcp_tool_calls_total,
    mcp_tool_duration_seconds,
)
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server
from src.core.metrics_middleware import MetricsMiddleware


class TestMetricsCounters:
    """Test counter metrics"""

    def test_record_http_request(self):
        """Test recording HTTP request"""
        initial = http_requests_total.labels(
            method="GET",
            endpoint="/health",
            status_code="200"
        )._value.get()

        record_http_request("GET", "/health", 200)

        final = http_requests_total.labels(
            method="GET",
            endpoint="/health",
            status_code="200"
        )._value.get()

        assert final > initial


class TestMetricsGauges:
    """Test gauge metrics"""

    def test_active_requests_increment_decrement(self):
        """Test incrementing and decrementing active requests"""
        initial = active_requests._value.get()

        increment_active_requests()
        assert active_requests._value.get() == initial + 1

        increment_active_requests()
        assert active_requests._value.get() == initial + 2

        decrement_active_requests()
        assert active_requests._value.get() == initial + 1

        decrement_active_requests()
        assert active_requests._value.get() == initial


class TestMetricsExport:
    """Test metrics export"""

    def test_get_metrics_returns_bytes(self):
        """Test that get_metrics returns bytes"""
        assert isinstance(get_metrics(), bytes)

    def test_get_metrics_contains_metric_names(self):
        """Test that exported metrics contain expected metric names"""
        metrics = get_metrics().decode('utf-8')
        assert "contex_http_requests_total" in metrics

    def test_get_metrics_prometheus_format(self):
        """Test that metrics are in Prometheus format"""
        metrics = get_metrics().decode('utf-8')
        assert "# HELP" in metrics
        assert "# TYPE" in metrics

    def test_no_id_labels_exposed(self):
        """No tenant/project IDs may appear as labels in the scrape output (#78)."""
        record_http_request("GET", "/health", 200)
        metrics = get_metrics().decode("utf-8")
        assert "tenant_id" not in metrics
        assert "project_id" not in metrics


def test_endpoint_label_is_the_route_template():
    router = APIRouter()
    router.add_api_route("/projects/{project_id}/data", lambda project_id: {})
    app = FastAPI()
    app.include_router(router, prefix="/sandbox")
    app.mount("/static", Starlette())
    app.add_middleware(MetricsMiddleware)
    client = TestClient(app)
    for path in ("/sandbox/projects/my-proj/data", "/static/no-such-file.js", "/wp-login.php"):
        client.get(path)

    labels = {s.labels["endpoint"] for s in http_requests_total.collect()[0].samples}
    assert {"/sandbox/projects/{project_id}/data", "/static", "other"} <= labels
    assert not any(word in label for label in labels for word in ("my-proj", "no-such", "wp-login"))


def _sample(metric, suffix="_total", **labels):
    return next(
        (s.value for s in metric.collect()[0].samples if s.name.endswith(suffix) and s.labels == labels),
        0,
    )


@pytest.mark.asyncio
async def test_mcp_tool_calls_are_counted_by_tool_and_outcome(db):
    engine = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await engine.semantic_matcher.initialize_index()
    server, _ = build_mcp_server(engine)
    before = {
        "ok": _sample(mcp_tool_calls_total, tool="contex_query", outcome="ok"),
        "error": _sample(mcp_tool_calls_total, tool="contex_query", outcome="error"),
        "unknown": _sample(mcp_tool_calls_total, tool="unknown", outcome="error"),
        "timed": _sample(mcp_tool_duration_seconds, "_count", tool="contex_query"),
    }

    await server.call_tool("contex_query", {"project_id": "p", "query": "anything"})
    with pytest.raises(ToolError):
        await server.call_tool("contex_query", {"project_id": "p"})
    with pytest.raises(ToolError):
        await server.call_tool("drop_tables_please", {})

    assert _sample(mcp_tool_calls_total, tool="contex_query", outcome="ok") == before["ok"] + 1
    assert _sample(mcp_tool_calls_total, tool="contex_query", outcome="error") == before["error"] + 1
    assert _sample(mcp_tool_calls_total, tool="unknown", outcome="error") == before["unknown"] + 1
    assert _sample(mcp_tool_duration_seconds, "_count", tool="contex_query") == before["timed"] + 2
    assert "drop_tables_please" not in get_metrics().decode()
