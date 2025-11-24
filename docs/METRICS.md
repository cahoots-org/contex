# Prometheus Metrics

Contex exposes Prometheus metrics for comprehensive monitoring and observability.

## Quick Start

### Access Metrics

```bash
# View metrics endpoint
curl http://localhost:8001/metrics
```

### Prometheus Configuration

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'contex'
    static_configs:
      - targets: ['localhost:8001']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

## Available Metrics

### Business Metrics (Counters)

#### Agent Metrics

**`contex_agents_registered_total{project_id, notification_method}`**
- Total number of agents registered
- Labels: `project_id`, `notification_method` (redis/webhook)

**`contex_agents_unregistered_total{project_id}`**
- Total number of agents unregistered
- Labels: `project_id`

#### Data Publishing Metrics

**`contex_events_published_total{project_id, data_format}`**
- Total number of events published
- Labels: `project_id`, `data_format` (json/yaml/toml/text)

#### Query Metrics

**`contex_queries_total{project_id, status}`**
- Total number of queries executed
- Labels: `project_id`, `status` (success/error)

#### Webhook Metrics

**`contex_webhooks_sent_total{status}`**
- Total number of webhooks sent
- Labels: `status` (success/error/timeout)

#### HTTP Metrics

**`contex_http_requests_total{method, endpoint, status_code}`**
- Total HTTP requests
- Labels: `method`, `endpoint`, `status_code`

#### Security Metrics

**`contex_auth_attempts_total{status}`**
- Total authentication attempts
- Labels: `status` (success/failure)

**`contex_rate_limit_exceeded_total{endpoint}`**
- Total rate limit exceeded events
- Labels: `endpoint`

**`contex_rbac_denials_total{role, permission}`**
- Total RBAC permission denials
- Labels: `role`, `permission`

### Performance Metrics (Histograms)

#### Operation Duration

**`contex_embedding_duration_seconds{operation}`**
- Time spent generating embeddings
- Labels: `operation` (encode/search)
- Buckets: 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0

**`contex_query_duration_seconds{project_id}`**
- Time spent executing queries
- Labels: `project_id`
- Buckets: 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0

**`contex_publish_duration_seconds{project_id}`**
- Time spent publishing data
- Labels: `project_id`
- Buckets: 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0

**`contex_registration_duration_seconds{project_id}`**
- Time spent registering agents
- Labels: `project_id`
- Buckets: 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0

**`contex_http_request_duration_seconds{method, endpoint}`**
- HTTP request duration
- Labels: `method`, `endpoint`
- Buckets: 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0

**`contex_redis_operation_duration_seconds{operation}`**
- Redis operation duration
- Labels: `operation`
- Buckets: 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1

### Resource Metrics (Gauges)

**`contex_registered_agents{project_id}`**
- Number of currently registered agents
- Labels: `project_id`

**`contex_redis_connections`**
- Number of active Redis connections

**`contex_memory_usage_bytes`**
- Memory usage in bytes

**`contex_active_requests`**
- Number of active HTTP requests

### Service Information

**`contex_service_info{version, service}`**
- Service metadata
- Labels: `version`, `service`

## Example Queries

### PromQL Queries

#### Request Rate

```promql
# Requests per second
rate(contex_http_requests_total[5m])

# Requests per second by endpoint
sum(rate(contex_http_requests_total[5m])) by (endpoint)

# Error rate
sum(rate(contex_http_requests_total{status_code=~"5.."}[5m])) by (endpoint)
```

#### Latency

```promql
# 95th percentile latency
histogram_quantile(0.95, rate(contex_http_request_duration_seconds_bucket[5m]))

# Average publish duration
rate(contex_publish_duration_seconds_sum[5m]) / rate(contex_publish_duration_seconds_count[5m])

# Slow requests (>1s)
histogram_quantile(0.99, rate(contex_http_request_duration_seconds_bucket[5m])) > 1
```

#### Business Metrics

```promql
# Agents registered per minute
rate(contex_agents_registered_total[1m]) * 60

# Events published per minute by project
sum(rate(contex_events_published_total[1m])) by (project_id) * 60

# Query success rate
sum(rate(contex_queries_total{status="success"}[5m])) / sum(rate(contex_queries_total[5m]))
```

#### Resource Usage

```promql
# Active agents by project
contex_registered_agents

# Active HTTP requests
contex_active_requests

# Memory usage in MB
contex_memory_usage_bytes / 1024 / 1024
```

## Grafana Dashboards

### Example Dashboard JSON

```json
{
  "dashboard": {
    "title": "Contex Metrics",
    "panels": [
      {
        "title": "Request Rate",
        "targets": [
          {
            "expr": "sum(rate(contex_http_requests_total[5m])) by (endpoint)"
          }
        ]
      },
      {
        "title": "P95 Latency",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(contex_http_request_duration_seconds_bucket[5m]))"
          }
        ]
      },
      {
        "title": "Active Agents",
        "targets": [
          {
            "expr": "sum(contex_registered_agents) by (project_id)"
          }
        ]
      }
    ]
  }
}
```

### Key Panels

1. **Request Rate**: `sum(rate(contex_http_requests_total[5m])) by (endpoint)`
2. **Error Rate**: `sum(rate(contex_http_requests_total{status_code=~"5.."}[5m]))`
3. **P95 Latency**: `histogram_quantile(0.95, rate(contex_http_request_duration_seconds_bucket[5m]))`
4. **Active Agents**: `sum(contex_registered_agents) by (project_id)`
5. **Events/min**: `sum(rate(contex_events_published_total[1m])) by (project_id) * 60`
6. **Memory Usage**: `contex_memory_usage_bytes / 1024 / 1024`

## Alerting Rules

### Example Alerts

```yaml
# prometheus-alerts.yml
groups:
  - name: contex_alerts
    interval: 30s
    rules:
      # High error rate
      - alert: HighErrorRate
        expr: |
          sum(rate(contex_http_requests_total{status_code=~"5.."}[5m])) 
          / sum(rate(contex_http_requests_total[5m])) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value | humanizePercentage }}"
      
      # High latency
      - alert: HighLatency
        expr: |
          histogram_quantile(0.95, 
            rate(contex_http_request_duration_seconds_bucket[5m])
          ) > 1.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High latency detected"
          description: "P95 latency is {{ $value }}s"
      
      # Rate limit exceeded frequently
      - alert: FrequentRateLimits
        expr: |
          rate(contex_rate_limit_exceeded_total[5m]) > 10
        for: 5m
        labels:
          severity: info
        annotations:
          summary: "Frequent rate limit exceeded"
          description: "Rate limits exceeded {{ $value }} times/sec"
      
      # No active agents
      - alert: NoActiveAgents
        expr: sum(contex_registered_agents) == 0
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "No active agents"
          description: "No agents registered for 10 minutes"
```

## Integration Examples

### Docker Compose

```yaml
version: '3.8'

services:
  contex:
    image: contex:latest
    ports:
      - "8001:8001"
  
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
  
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-storage:/var/lib/grafana

volumes:
  grafana-storage:
```

### Kubernetes

```yaml
apiVersion: v1
kind: Service
metadata:
  name: contex
  labels:
    app: contex
spec:
  ports:
    - port: 8001
      name: http
  selector:
    app: contex
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: contex
spec:
  selector:
    matchLabels:
      app: contex
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
```

## Best Practices

### 1. Monitor Key Metrics

Focus on:
- **Request rate**: Track traffic patterns
- **Error rate**: Detect issues early
- **Latency**: Ensure performance SLAs
- **Active agents**: Monitor system usage

### 2. Set Up Alerts

Create alerts for:
- Error rate > 5%
- P95 latency > 1s
- No active agents for > 10min
- Frequent rate limit exceeded

### 3. Use Labels Wisely

- Use `project_id` to track per-project metrics
- Use `endpoint` to identify slow endpoints
- Use `status_code` to track errors

### 4. Retention and Storage

- Keep high-resolution data for 15 days
- Downsample to 5min resolution for 90 days
- Archive monthly summaries for 1 year

### 5. Dashboard Organization

Create dashboards for:
- **Overview**: Request rate, errors, latency
- **Business**: Agents, events, queries
- **Performance**: Latency histograms, slow endpoints
- **Resources**: Memory, connections, active requests

## Troubleshooting

### Metrics Not Appearing

1. **Check endpoint**: `curl http://localhost:8001/metrics`
2. **Verify Prometheus config**: Check `prometheus.yml`
3. **Check Prometheus targets**: Visit `http://localhost:9090/targets`

### High Cardinality

If you see too many unique label combinations:
- Avoid using IDs in labels
- Use endpoint normalization (already implemented)
- Limit project_id cardinality

### Missing Metrics

- Ensure middleware is registered in `main.py`
- Check that operations are being tracked
- Verify metrics are being recorded in code

## Performance Impact

Metrics collection has minimal overhead:
- Counter increment: ~100ns
- Histogram observation: ~1-2μs
- Gauge update: ~100ns

Total overhead: <1% of request time

## Related Documentation

- [Structured Logging](LOGGING.md)
- [Health Checks](../README.md#health-checks)
- [API Documentation](http://localhost:8001/docs)

## Summary

Contex Prometheus metrics provide:

✅ **Business metrics** - Agents, events, queries  
✅ **Performance metrics** - Latency histograms  
✅ **Resource metrics** - Memory, connections  
✅ **Security metrics** - Auth, rate limits, RBAC  
✅ **HTTP metrics** - Requests, errors, duration  
✅ **Production-ready** - Prometheus/Grafana compatible  

All metrics are automatically collected and ready for monitoring platforms.
