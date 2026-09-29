"""Tests for Prometheus metrics"""

from src.core.metrics import (
    http_requests_total,
    active_requests,
    record_http_request,
    increment_active_requests,
    decrement_active_requests,
    record_tenant_request,
    get_metrics,
)


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

    def test_record_tenant_request_has_no_tenant_id_label(self):
        """Tenant request metric is aggregate: no tenant_id label (see #78)."""
        record_tenant_request("GET", "/x")
        assert "tenant_id" not in get_metrics().decode("utf-8")


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
        assert "contex_tenant_requests_total" in metrics

    def test_get_metrics_prometheus_format(self):
        """Test that metrics are in Prometheus format"""
        metrics = get_metrics().decode('utf-8')
        assert "# HELP" in metrics
        assert "# TYPE" in metrics

    def test_no_id_labels_exposed(self):
        """No tenant/project IDs may appear as labels in the scrape output (#78)."""
        record_tenant_request("GET", "/x")
        record_http_request("GET", "/health", 200)
        metrics = get_metrics().decode("utf-8")
        assert "tenant_id" not in metrics
        assert "project_id" not in metrics


class TestMetricsMiddleware:
    """Test metrics middleware"""

    def test_endpoint_normalization(self):
        """Test that endpoints are normalized correctly"""
        from src.core.metrics_middleware import MetricsMiddleware

        middleware = MetricsMiddleware(None)

        assert middleware._normalize_endpoint("/sandbox/agents/550e8400-e29b-41d4-a716-446655440000") == "/sandbox/agents/{id}"
        assert middleware._normalize_endpoint("/sandbox/projects/123/data") == "/sandbox/projects/{id}/data"
        assert middleware._normalize_endpoint("/sandbox/agents/agent-123") == "/sandbox/agents/{id}"
        assert middleware._normalize_endpoint("/health") == "/health"

    def test_looks_like_id(self):
        """Test ID detection"""
        from src.core.metrics_middleware import MetricsMiddleware

        middleware = MetricsMiddleware(None)

        assert middleware._looks_like_id("550e8400-e29b-41d4-a716-446655440000")  # UUID
        assert middleware._looks_like_id("123")  # Numeric
        assert middleware._looks_like_id("agent-123")  # Prefixed
        assert middleware._looks_like_id("proj-abc123")  # Prefixed

        assert not middleware._looks_like_id("health")
        assert not middleware._looks_like_id("publish")
        assert not middleware._looks_like_id("agents")
