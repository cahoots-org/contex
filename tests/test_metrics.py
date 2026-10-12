"""Tests for Prometheus metrics"""

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from src.core.metrics import (
    http_requests_total,
    active_requests,
    record_http_request,
    increment_active_requests,
    decrement_active_requests,
    get_metrics,
)
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
