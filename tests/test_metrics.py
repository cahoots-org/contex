"""Tests for Prometheus metrics"""

import pytest
from prometheus_client import REGISTRY
from src.core.metrics import (
    # Counters
    agents_registered_total,
    events_published_total,
    queries_total,
    webhooks_sent_total,
    http_requests_total,
    # Histograms
    embedding_duration_seconds,
    query_duration_seconds,
    publish_duration_seconds,
    # Gauges
    registered_agents,
    redis_connections,
    active_requests,
    # Functions
    record_agent_registered,
    record_event_published,
    record_query,
    record_webhook_sent,
    record_http_request,
    update_registered_agents_count,
    update_redis_connections,
    increment_active_requests,
    decrement_active_requests,
    get_metrics,
)


class TestMetricsCounters:
    """Test counter metrics"""
    
    def test_record_agent_registered(self):
        """Test recording agent registration"""
        initial = agents_registered_total.labels(
            project_id="test-proj",
            notification_method="redis"
        )._value.get()
        
        record_agent_registered("test-proj", "redis")
        
        final = agents_registered_total.labels(
            project_id="test-proj",
            notification_method="redis"
        )._value.get()
        
        assert final > initial
    
    def test_record_event_published(self):
        """Test recording event publication"""
        initial = events_published_total.labels(
            project_id="test-proj",
            data_format="json"
        )._value.get()
        
        record_event_published("test-proj", "json")
        
        final = events_published_total.labels(
            project_id="test-proj",
            data_format="json"
        )._value.get()
        
        assert final > initial
    
    def test_record_query(self):
        """Test recording query execution"""
        initial = queries_total.labels(
            project_id="test-proj",
            status="success"
        )._value.get()
        
        record_query("test-proj", "success")
        
        final = queries_total.labels(
            project_id="test-proj",
            status="success"
        )._value.get()
        
        assert final > initial
    
    def test_record_webhook_sent(self):
        """Test recording webhook sent"""
        initial = webhooks_sent_total.labels(status="success")._value.get()
        
        record_webhook_sent("success")
        
        final = webhooks_sent_total.labels(status="success")._value.get()
        
        assert final > initial
    
    def test_record_http_request(self):
        """Test recording HTTP request"""
        initial = http_requests_total.labels(
            method="GET",
            endpoint="/api/v1/health",
            status_code="200"
        )._value.get()
        
        record_http_request("GET", "/api/v1/health", 200)
        
        final = http_requests_total.labels(
            method="GET",
            endpoint="/api/v1/health",
            status_code="200"
        )._value.get()
        
        assert final > initial


class TestMetricsHistograms:
    """Test histogram metrics"""
    
    def test_publish_duration_histogram(self):
        """Test publish duration histogram"""
        # Record some durations
        publish_duration_seconds.labels(project_id="test-proj-hist").observe(0.1)
        publish_duration_seconds.labels(project_id="test-proj-hist").observe(0.2)
        publish_duration_seconds.labels(project_id="test-proj-hist").observe(0.3)
        
        # Check that observations were recorded by getting metrics output
        metrics = get_metrics().decode('utf-8')
        assert 'contex_publish_duration_seconds' in metrics
        assert 'project_id="test-proj-hist"' in metrics
    
    def test_query_duration_histogram(self):
        """Test query duration histogram"""
        query_duration_seconds.labels(project_id="test-proj-query").observe(0.05)
        
        metrics = get_metrics().decode('utf-8')
        assert 'contex_query_duration_seconds' in metrics
        assert 'project_id="test-proj-query"' in metrics
    
    def test_embedding_duration_histogram(self):
        """Test embedding duration histogram"""
        embedding_duration_seconds.labels(operation="encode").observe(0.01)
        
        metrics = get_metrics().decode('utf-8')
        assert 'contex_embedding_duration_seconds' in metrics
        assert 'operation="encode"' in metrics


class TestMetricsGauges:
    """Test gauge metrics"""
    
    def test_update_registered_agents_count(self):
        """Test updating registered agents count"""
        update_registered_agents_count("test-proj", 5)
        
        value = registered_agents.labels(project_id="test-proj")._value.get()
        assert value == 5
        
        update_registered_agents_count("test-proj", 10)
        value = registered_agents.labels(project_id="test-proj")._value.get()
        assert value == 10
    
    def test_update_redis_connections(self):
        """Test updating Redis connections"""
        update_redis_connections(3)
        assert redis_connections._value.get() == 3
        
        update_redis_connections(5)
        assert redis_connections._value.get() == 5
    
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
        metrics = get_metrics()
        assert isinstance(metrics, bytes)
    
    def test_get_metrics_contains_metric_names(self):
        """Test that exported metrics contain expected metric names"""
        metrics = get_metrics().decode('utf-8')
        
        # Check for some key metrics
        assert "contex_agents_registered_total" in metrics
        assert "contex_events_published_total" in metrics
        assert "contex_http_requests_total" in metrics
        assert "contex_registered_agents" in metrics
    
    def test_get_metrics_prometheus_format(self):
        """Test that metrics are in Prometheus format"""
        metrics = get_metrics().decode('utf-8')
        
        # Should contain HELP and TYPE lines
        assert "# HELP" in metrics
        assert "# TYPE" in metrics
    
    def test_service_info_in_metrics(self):
        """Test that service info is included"""
        metrics = get_metrics().decode('utf-8')
        
        assert "contex_service_info" in metrics
        assert 'version="0.2.0"' in metrics


class TestMetricsLabels:
    """Test metrics with different labels"""
    
    def test_metrics_with_different_projects(self):
        """Test that metrics are tracked separately per project"""
        record_event_published("proj-1", "json")
        record_event_published("proj-2", "json")
        record_event_published("proj-1", "json")
        
        proj1_count = events_published_total.labels(
            project_id="proj-1",
            data_format="json"
        )._value.get()
        
        proj2_count = events_published_total.labels(
            project_id="proj-2",
            data_format="json"
        )._value.get()
        
        # proj-1 should have more events
        assert proj1_count > proj2_count
    
    def test_metrics_with_different_formats(self):
        """Test that metrics track different data formats"""
        record_event_published("test-proj", "json")
        record_event_published("test-proj", "yaml")
        record_event_published("test-proj", "json")
        
        json_count = events_published_total.labels(
            project_id="test-proj",
            data_format="json"
        )._value.get()
        
        yaml_count = events_published_total.labels(
            project_id="test-proj",
            data_format="yaml"
        )._value.get()
        
        # JSON should have more events
        assert json_count > yaml_count
    
    def test_metrics_with_different_notification_methods(self):
        """Test that metrics track different notification methods"""
        record_agent_registered("test-proj", "redis")
        record_agent_registered("test-proj", "webhook")
        record_agent_registered("test-proj", "redis")
        
        redis_count = agents_registered_total.labels(
            project_id="test-proj",
            notification_method="redis"
        )._value.get()
        
        webhook_count = agents_registered_total.labels(
            project_id="test-proj",
            notification_method="webhook"
        )._value.get()
        
        # Redis should have more registrations
        assert redis_count > webhook_count


class TestMetricsMiddleware:
    """Test metrics middleware"""
    
    def test_endpoint_normalization(self):
        """Test that endpoints are normalized correctly"""
        from src.core.metrics_middleware import MetricsMiddleware
        
        middleware = MetricsMiddleware(None)
        
        # Test UUID normalization
        assert middleware._normalize_endpoint("/api/v1/agents/550e8400-e29b-41d4-a716-446655440000") == "/api/v1/agents/{id}"
        
        # Test numeric ID normalization
        assert middleware._normalize_endpoint("/api/v1/projects/123/data") == "/api/v1/projects/{id}/data"
        
        # Test prefix ID normalization
        assert middleware._normalize_endpoint("/api/v1/agents/agent-123") == "/api/v1/agents/{id}"
        
        # Test normal paths
        assert middleware._normalize_endpoint("/api/v1/health") == "/api/v1/health"
        assert middleware._normalize_endpoint("/api/v1/data/publish") == "/api/v1/data/publish"
    
    def test_looks_like_id(self):
        """Test ID detection"""
        from src.core.metrics_middleware import MetricsMiddleware
        
        middleware = MetricsMiddleware(None)
        
        # Should detect as IDs
        assert middleware._looks_like_id("550e8400-e29b-41d4-a716-446655440000")  # UUID
        assert middleware._looks_like_id("123")  # Numeric
        assert middleware._looks_like_id("agent-123")  # Prefixed
        assert middleware._looks_like_id("proj-abc123")  # Prefixed
        
        # Should not detect as IDs
        assert not middleware._looks_like_id("health")
        assert not middleware._looks_like_id("publish")
        assert not middleware._looks_like_id("agents")
