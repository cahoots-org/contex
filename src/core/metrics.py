"""Prometheus metrics for Contex.

Only metrics that are actually populated by live code paths are defined here:
HTTP request metrics (via MetricsMiddleware) and tenant-scoped counters (via
the tenant middlewares). No label carries a tenant or project ID, so a
/metrics scraper cannot enumerate them (#78).
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY

registry = REGISTRY

# ---------------------------------------------------------------------------
# HTTP metrics — populated by MetricsMiddleware. Endpoints are normalized
# (dynamic IDs collapsed to {id}) before they reach these labels.
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

# ---------------------------------------------------------------------------
# Tenant metrics — populated by the tenant middlewares. tenant_id is
# deliberately NOT a label: exposing raw tenant IDs via /metrics let a scraper
# enumerate every tenant (#78).
# ---------------------------------------------------------------------------
tenant_requests_total = Counter(
    'contex_tenant_requests_total',
    'Total tenant-scoped requests',
    ['method', 'endpoint'],
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


def record_tenant_request(method: str, endpoint: str):
    """Record a tenant-scoped request (tenant_id not labeled; see #78)."""
    tenant_requests_total.labels(method=method, endpoint=endpoint).inc()
