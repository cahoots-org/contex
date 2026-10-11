"""Prometheus metrics for Contex.

HTTP request metrics, recorded by MetricsMiddleware. Endpoints are labeled by
route template, so no label carries a tenant or project ID.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY

registry = REGISTRY

# ---------------------------------------------------------------------------
# HTTP metrics, populated by MetricsMiddleware.
# ---------------------------------------------------------------------------
http_requests_total = Counter(
    'contex_http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code'],
    registry=registry,
)

http_request_duration_seconds = Histogram(
    'contex_http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

active_requests = Gauge(
    'contex_active_requests',
    'Number of active HTTP requests',
    registry=registry,
)

def get_metrics() -> bytes:
    """Return current metrics in Prometheus exposition format."""
    return generate_latest(registry)


def record_http_request(method: str, endpoint: str, status_code: int):
    """Record an HTTP request."""
    http_requests_total.labels(
        method=method, endpoint=endpoint, status_code=str(status_code)
    ).inc()


def increment_active_requests():
    """Increment the in-flight request gauge."""
    active_requests.inc()


def decrement_active_requests():
    """Decrement the in-flight request gauge."""
    active_requests.dec()

