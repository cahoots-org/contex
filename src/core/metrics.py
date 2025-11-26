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

# Circuit breaker state (0=closed, 1=half_open, 2=open)
circuit_breaker_state = Gauge(
    'contex_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=half_open, 2=open)',
    ['name'],
    registry=registry
)

# Circuit breaker failures
circuit_breaker_failures_total = Counter(
    'contex_circuit_breaker_failures_total',
    'Total circuit breaker failures',
    ['name'],
    registry=registry
)

# Circuit breaker successes
circuit_breaker_successes_total = Counter(
    'contex_circuit_breaker_successes_total',
    'Total circuit breaker successes',
    ['name'],
    registry=registry
)

# Circuit breaker state transitions
circuit_breaker_transitions_total = Counter(
    'contex_circuit_breaker_transitions_total',
    'Total circuit breaker state transitions',
    ['name', 'from_state', 'to_state'],
    registry=registry
)

# Embedding cache hits
embedding_cache_hits_total = Counter(
    'contex_embedding_cache_hits_total',
    'Total embedding cache hits',
    registry=registry
)

# Embedding cache misses
embedding_cache_misses_total = Counter(
    'contex_embedding_cache_misses_total',
    'Total embedding cache misses',
    registry=registry
)

# Embedding cache size
embedding_cache_size = Gauge(
    'contex_embedding_cache_size',
    'Number of entries in embedding cache',
    registry=registry
)

# ============================================================================
# RETRY METRICS
# ============================================================================

# Webhook retry counter
webhook_retries_total = Counter(
    'contex_webhook_retries_total',
    'Total number of webhook retry attempts',
    registry=registry
)

# Webhook retry delay histogram
webhook_retry_delay_seconds = Histogram(
    'contex_webhook_retry_delay_seconds',
    'Webhook retry delay in seconds',
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0),
    registry=registry
)

# Generic retry counter (for other operations)
retries_total = Counter(
    'contex_retries_total',
    'Total number of retry attempts',
    ['operation'],
    registry=registry
)

# Retry exhaustion counter
retry_exhausted_total = Counter(
    'contex_retry_exhausted_total',
    'Total number of retry exhaustions (all attempts failed)',
    ['operation'],
    registry=registry
)

# ============================================================================
# GRACEFUL DEGRADATION METRICS
# ============================================================================

# Degradation mode gauge (0=normal, 1=degraded, 2=readonly, 3=unavailable)
degradation_mode = Gauge(
    'contex_degradation_mode',
    'Current degradation mode (0=normal, 1=degraded, 2=readonly, 3=unavailable)',
    registry=registry
)

# Degradation events counter
degradation_events_total = Counter(
    'contex_degradation_events_total',
    'Total degradation mode transitions',
    ['from_mode', 'to_mode'],
    registry=registry
)

# Fallback cache stats
fallback_cache_hits_total = Counter(
    'contex_fallback_cache_hits_total',
    'Total fallback cache hits during degradation',
    registry=registry
)

fallback_cache_misses_total = Counter(
    'contex_fallback_cache_misses_total',
    'Total fallback cache misses during degradation',
    registry=registry
)

# ============================================================================
# TENANT METRICS
# ============================================================================

# Tenant request counter
tenant_requests_total = Counter(
    'contex_tenant_requests_total',
    'Total requests per tenant',
    ['tenant_id', 'method', 'endpoint'],
    registry=registry
)

# Tenant error counter
tenant_errors_total = Counter(
    'contex_tenant_errors_total',
    'Total errors per tenant',
    ['tenant_id', 'error_type'],
    registry=registry
)

# Tenant quota usage gauge
tenant_quota_usage = Gauge(
    'contex_tenant_quota_usage',
    'Tenant quota usage as percentage',
    ['tenant_id', 'resource'],
    registry=registry
)

# Tenant resource usage counters
tenant_projects_total = Gauge(
    'contex_tenant_projects_total',
    'Number of projects per tenant',
    ['tenant_id'],
    registry=registry
)

tenant_agents_total = Gauge(
    'contex_tenant_agents_total',
    'Number of agents per tenant',
    ['tenant_id'],
    registry=registry
)

tenant_api_keys_total = Gauge(
    'contex_tenant_api_keys_total',
    'Number of API keys per tenant',
    ['tenant_id'],
    registry=registry
)

tenant_events_this_month = Gauge(
    'contex_tenant_events_month',
    'Events published this month per tenant',
    ['tenant_id'],
    registry=registry
)

tenant_storage_used_mb = Gauge(
    'contex_tenant_storage_mb',
    'Storage used in MB per tenant',
    ['tenant_id'],
    registry=registry
)

# Tenant quota exceeded counter
tenant_quota_exceeded_total = Counter(
    'contex_tenant_quota_exceeded_total',
    'Total quota exceeded events per tenant',
    ['tenant_id', 'resource'],
    registry=registry
)

# Tenant plan gauge (for plan distribution)
tenant_plan = Gauge(
    'contex_tenant_plan',
    'Tenant plan (1=free, 2=starter, 3=pro, 4=enterprise)',
    ['tenant_id', 'plan'],
    registry=registry
)

# Total tenants gauge
tenants_total = Gauge(
    'contex_tenants_total',
    'Total number of tenants',
    ['plan', 'active'],
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


def record_retry(operation: str):
    """Record a retry attempt for an operation"""
    retries_total.labels(operation=operation).inc()


def record_retry_exhausted(operation: str):
    """Record when all retry attempts have been exhausted"""
    retry_exhausted_total.labels(operation=operation).inc()


# ============================================================================
# TENANT METRIC RECORDING FUNCTIONS
# ============================================================================

def record_tenant_request(tenant_id: str, method: str, endpoint: str):
    """Record a request for a tenant"""
    tenant_requests_total.labels(
        tenant_id=tenant_id,
        method=method,
        endpoint=endpoint
    ).inc()


def record_tenant_error(tenant_id: str, error_type: str):
    """Record an error for a tenant"""
    tenant_errors_total.labels(
        tenant_id=tenant_id,
        error_type=error_type
    ).inc()


def update_tenant_quota_usage(tenant_id: str, resource: str, percentage: float):
    """Update tenant quota usage percentage"""
    tenant_quota_usage.labels(
        tenant_id=tenant_id,
        resource=resource
    ).set(percentage)


def update_tenant_resource_usage(
    tenant_id: str,
    projects: int = None,
    agents: int = None,
    api_keys: int = None,
    events_month: int = None,
    storage_mb: float = None,
):
    """Update tenant resource usage metrics"""
    if projects is not None:
        tenant_projects_total.labels(tenant_id=tenant_id).set(projects)
    if agents is not None:
        tenant_agents_total.labels(tenant_id=tenant_id).set(agents)
    if api_keys is not None:
        tenant_api_keys_total.labels(tenant_id=tenant_id).set(api_keys)
    if events_month is not None:
        tenant_events_this_month.labels(tenant_id=tenant_id).set(events_month)
    if storage_mb is not None:
        tenant_storage_used_mb.labels(tenant_id=tenant_id).set(storage_mb)


def record_tenant_quota_exceeded(tenant_id: str, resource: str):
    """Record a quota exceeded event for a tenant"""
    tenant_quota_exceeded_total.labels(
        tenant_id=tenant_id,
        resource=resource
    ).inc()


def update_tenant_plan_metric(tenant_id: str, plan: str):
    """Update tenant plan metric"""
    # Reset all plans for this tenant, then set the current one
    for p in ['free', 'starter', 'pro', 'enterprise']:
        tenant_plan.labels(tenant_id=tenant_id, plan=p).set(0)
    tenant_plan.labels(tenant_id=tenant_id, plan=plan).set(1)


def update_tenants_total(plan: str, active: bool, count: int):
    """Update total tenants count"""
    tenants_total.labels(
        plan=plan,
        active=str(active).lower()
    ).set(count)
