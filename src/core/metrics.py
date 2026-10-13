"""Prometheus metrics for Contex.

HTTP request metrics, recorded by MetricsMiddleware, and MCP metrics, recorded
by the MCP adapter. Endpoints are labeled by route template and tools by name,
so no label carries a tenant or project ID.
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

# ---------------------------------------------------------------------------
# MCP metrics. All MCP traffic shares the one /mcp HTTP endpoint label.
# ---------------------------------------------------------------------------
mcp_tool_calls_total = Counter(
    'contex_mcp_tool_calls_total',
    'MCP tool calls, by tool and outcome (ok or error)',
    ['tool', 'outcome'],
    registry=registry,
)

mcp_tool_duration_seconds = Histogram(
    'contex_mcp_tool_duration_seconds',
    'MCP tool call duration',
    ['tool'],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

mcp_listen_streams = Gauge(
    'contex_mcp_listen_streams',
    'Open MCP subscriptions/listen streams',
    registry=registry,
)

mcp_resource_updates_total = Counter(
    'contex_mcp_resource_updates_total',
    'resources/updated notifications published to MCP subscribers',
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



def record_mcp_tool_call(tool: str, outcome: str, seconds: float):
    """Record one MCP tool call."""
    mcp_tool_calls_total.labels(tool=tool, outcome=outcome).inc()
    mcp_tool_duration_seconds.labels(tool=tool).observe(seconds)
