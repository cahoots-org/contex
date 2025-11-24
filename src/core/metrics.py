"""Prometheus metrics for Contex"""

from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, REGISTRY
from prometheus_client.core import CollectorRegistry
import time
from typing import Optional
from functools import wraps

# Create custom registry for better control
registry = REGISTRY

# Info metric for service information
service_info = Info('contex_service', 'Contex service information', registry=registry)
service_info.info({
    'version': '0.2.0',
    'service': 'contex'
})

# ============================================================================
# BUSINESS METRICS - Counters
# ============================================================================

# Agent metrics
agents_registered_total = Counter(
    'contex_agents_registered_total',
    'Total number of agents registered',
    ['project_id', 'notification_method'],
    registry=registry
)

agents_unregistered_total = Counter(
    'contex_agents_unregistered_total',
    'Total number of agents unregistered',
    ['project_id'],
    registry=registry
)

# Data publishing metrics
events_published_total = Counter(
    'contex_events_published_total',
    'Total number of events published',
    ['project_id', 'data_format'],
    registry=registry
)

# Query metrics
queries_total = Counter(
    'contex_queries_total',
    'Total number of queries executed',
    ['project_id', 'status'],
    registry=registry
)

# Webhook metrics
webhooks_sent_total = Counter(
    'contex_webhooks_sent_total',
    'Total number of webhooks sent',
    ['status'],
    registry=registry
)

# HTTP metrics
http_requests_total = Counter(
    'contex_http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'status_code'],
    registry=registry
)

# Authentication metrics
auth_attempts_total = Counter(
    'contex_auth_attempts_total',
    'Total authentication attempts',
    ['status'],
    registry=registry
)

# Rate limit metrics
rate_limit_exceeded_total = Counter(
    'contex_rate_limit_exceeded_total',
    'Total rate limit exceeded events',
    ['endpoint'],
    registry=registry
)

# RBAC metrics
rbac_denials_total = Counter(
    'contex_rbac_denials_total',
    'Total RBAC permission denials',
    ['role', 'permission'],
    registry=registry
)

# ============================================================================
# PERFORMANCE METRICS - Histograms
# ============================================================================

# Embedding performance
embedding_duration_seconds = Histogram(
    'contex_embedding_duration_seconds',
    'Time spent generating embeddings',
    ['operation'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry
)

# Query performance
query_duration_seconds = Histogram(
    'contex_query_duration_seconds',
    'Time spent executing queries',
    ['project_id'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry
)

# Publish performance
publish_duration_seconds = Histogram(
    'contex_publish_duration_seconds',
    'Time spent publishing data',
    ['project_id'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry
)

# Registration performance
registration_duration_seconds = Histogram(
    'contex_registration_duration_seconds',
    'Time spent registering agents',
    ['project_id'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry
)

# HTTP request duration
http_request_duration_seconds = Histogram(
    'contex_http_request_duration_seconds',
    'HTTP request duration',
    ['method', 'endpoint'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry
)

# Redis operation duration
redis_operation_duration_seconds = Histogram(
    'contex_redis_operation_duration_seconds',
    'Redis operation duration',
    ['operation'],
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1),
    registry=registry
)

# ============================================================================
# RESOURCE METRICS - Gauges
# ============================================================================

# Active agents
registered_agents = Gauge(
    'contex_registered_agents',
    'Number of currently registered agents',
    ['project_id'],
    registry=registry
)

# Redis connections
redis_connections = Gauge(
    'contex_redis_connections',
    'Number of active Redis connections',
    registry=registry
)

# Memory usage (will be updated by monitoring)
memory_usage_bytes = Gauge(
    'contex_memory_usage_bytes',
    'Memory usage in bytes',
    registry=registry
)

# Active requests
active_requests = Gauge(
    'contex_active_requests',
    'Number of active HTTP requests',
    registry=registry
)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def track_time(histogram: Histogram, labels: Optional[dict] = None):
    """
    Decorator to track execution time.
    
    Usage:
        @track_time(query_duration_seconds, {'project_id': 'proj-123'})
        async def my_function():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                if labels:
                    histogram.labels(**labels).observe(duration)
                else:
                    histogram.observe(duration)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start
                if labels:
                    histogram.labels(**labels).observe(duration)
                else:
                    histogram.observe(duration)
        
        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def get_metrics() -> bytes:
    """Get current metrics in Prometheus format"""
    return generate_latest(registry)


# ============================================================================
# METRIC RECORDING FUNCTIONS
# ============================================================================

def record_agent_registered(project_id: str, notification_method: str):
    """Record agent registration"""
    agents_registered_total.labels(
        project_id=project_id,
        notification_method=notification_method
    ).inc()


def record_agent_unregistered(project_id: str):
    """Record agent unregistration"""
    agents_unregistered_total.labels(project_id=project_id).inc()


def record_event_published(project_id: str, data_format: str):
    """Record event publication"""
    events_published_total.labels(
        project_id=project_id,
        data_format=data_format
    ).inc()


def record_query(project_id: str, status: str):
    """Record query execution"""
    queries_total.labels(project_id=project_id, status=status).inc()


def record_webhook_sent(status: str):
    """Record webhook sent"""
    webhooks_sent_total.labels(status=status).inc()


def record_http_request(method: str, endpoint: str, status_code: int):
    """Record HTTP request"""
    http_requests_total.labels(
        method=method,
        endpoint=endpoint,
        status_code=str(status_code)
    ).inc()


def record_auth_attempt(status: str):
    """Record authentication attempt"""
    auth_attempts_total.labels(status=status).inc()


def record_rate_limit_exceeded(endpoint: str):
    """Record rate limit exceeded"""
    rate_limit_exceeded_total.labels(endpoint=endpoint).inc()


def record_rbac_denial(role: str, permission: str):
    """Record RBAC denial"""
    rbac_denials_total.labels(role=role, permission=permission).inc()


def update_registered_agents_count(project_id: str, count: int):
    """Update registered agents gauge"""
    registered_agents.labels(project_id=project_id).set(count)


def update_redis_connections(count: int):
    """Update Redis connections gauge"""
    redis_connections.set(count)


def update_memory_usage(bytes_used: int):
    """Update memory usage gauge"""
    memory_usage_bytes.set(bytes_used)


def increment_active_requests():
    """Increment active requests counter"""
    active_requests.inc()


def decrement_active_requests():
    """Decrement active requests counter"""
    active_requests.dec()
