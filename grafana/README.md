# Grafana Dashboards for Contex

Pre-built Grafana dashboards for monitoring Contex semantic context routing.

## 📊 Available Dashboards

### 1. Contex - Overview
**File:** `dashboards/contex-overview.json`

Main operational dashboard with:
- **Service Health**: Request rates, errors, active connections
- **Business Metrics**: Agents, events, queries
- **Performance**: Latency percentiles (p50, p95)
- **Cache Performance**: Hit rates, operations, size

**Recommended for:** Day-to-day operations, NOC displays

**Refresh Rate:** 30 seconds

### 2. Contex - Reliability & Errors
**File:** `dashboards/contex-reliability.json`

Deep dive into system reliability:
- **Circuit Breakers**: States, failures, transitions
- **Webhooks**: Success rates, status distribution
- **Authentication**: Auth attempts, rate limits, RBAC denials
- **Redis Operations**: Latency, connections, memory
- **Error Tracking**: HTTP errors, failed queries

**Recommended for:** Incident response, debugging, SRE

**Refresh Rate:** 1 minute

**Alerts Included:**
- Circuit breaker open
- High error rate
- Redis connection pool exhausted
- High rate limit violations

### 3. Contex - Business Metrics
**File:** `dashboards/contex-business.json`

Business KPIs and usage analytics:
- **Usage Overview**: Total agents, events, queries
- **Agent Activity**: Registrations, notification methods
- **Data Publishing**: Formats, top projects
- **Query Activity**: Success rates, latency
- **Performance KPIs**: Cache hit rate, availability
- **Growth Metrics**: 7-day trends

**Recommended for:** Product management, business reviews

**Refresh Rate:** 5 minutes

## 🚀 Quick Start

### Option 1: Manual Import

1. Open Grafana UI
2. Navigate to **Dashboards** → **Import**
3. Upload JSON file or paste content
4. Select Prometheus datasource
5. Click **Import**

### Option 2: Automatic Provisioning

1. Copy dashboards to Grafana provisioning directory:
```bash
cp grafana/dashboards/*.json /etc/grafana/provisioning/dashboards/
```

2. Create provisioning config:
```bash
cp grafana/provisioning/dashboards.yml /etc/grafana/provisioning/dashboards/
```

3. Restart Grafana:
```bash
systemctl restart grafana-server
```

Dashboards will be automatically loaded and updated.

### Option 3: Docker Compose

Add to your `docker-compose.yml`:

```yaml
services:
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./grafana/provisioning:/etc/grafana/provisioning/dashboards
      - grafana-data:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/etc/grafana/provisioning/dashboards/contex-overview.json

volumes:
  grafana-data:
```

## 📈 Metrics Reference

All dashboards use Prometheus metrics from Contex's `/api/v1/metrics` endpoint.

### Key Metrics

#### Business Metrics
- `contex_agents_registered_total` - Total agent registrations
- `contex_events_published_total` - Total events published
- `contex_queries_total` - Total queries executed
- `contex_registered_agents` - Currently active agents

#### Performance Metrics
- `contex_query_duration_seconds` - Query latency histogram
- `contex_publish_duration_seconds` - Publish latency histogram
- `contex_embedding_duration_seconds` - Embedding generation time
- `contex_http_request_duration_seconds` - HTTP request latency

#### Cache Metrics
- `contex_embedding_cache_hits_total` - Cache hits
- `contex_embedding_cache_misses_total` - Cache misses
- `contex_embedding_cache_size` - Number of cached entries

#### Reliability Metrics
- `contex_circuit_breaker_state` - Circuit breaker state (0/1/2)
- `contex_circuit_breaker_failures_total` - Breaker failures
- `contex_webhooks_sent_total` - Webhook delivery attempts
- `contex_auth_attempts_total` - Authentication attempts
- `contex_rate_limit_exceeded_total` - Rate limit violations

#### System Metrics
- `contex_redis_connections` - Active Redis connections
- `contex_memory_usage_bytes` - Memory usage
- `contex_active_requests` - In-flight requests

## 🎯 Recommended Alerts

### Critical (P1)
- Circuit breaker open for >5 minutes
- Error rate >5% for >5 minutes
- Redis connection pool >90% for >2 minutes
- p95 latency >1s for >5 minutes

### Warning (P2)
- Error rate >2% for >10 minutes
- Cache hit rate <50% for >15 minutes
- Webhook failure rate >5% for >10 minutes
- Rate limit violations >10/min

### Info (P3)
- Circuit breaker state transitions
- Large increase in agent registrations
- Unusual query patterns

## 🔧 Customization

### Adding Custom Panels

1. Edit dashboard JSON
2. Add panel to appropriate row:
```json
{
  "id": 99,
  "title": "My Custom Metric",
  "type": "graph",
  "targets": [
    {
      "expr": "your_prometheus_query",
      "legendFormat": "{{label}}"
    }
  ]
}
```

### Modifying Time Ranges

Default time ranges:
- Overview: Last 1 hour
- Reliability: Last 6 hours
- Business: Last 24 hours

Change in dashboard JSON:
```json
"time": {
  "from": "now-6h",
  "to": "now"
}
```

### Adjusting Thresholds

Modify alert thresholds in panel configuration:
```json
"alert": {
  "conditions": [
    {
      "evaluator": {
        "type": "gt",
        "params": [threshold_value]
      }
    }
  ]
}
```

## 📱 Mobile Support

All dashboards are responsive and work on mobile devices. For optimal mobile experience:

1. Use Grafana mobile app
2. Enable "Fit panels" in dashboard settings
3. Adjust time range for faster loading

## 🐛 Troubleshooting

### No Data Showing

**Check:**
1. Prometheus is scraping Contex metrics:
   ```bash
   curl http://localhost:8001/api/v1/metrics
   ```

2. Grafana datasource is configured:
   - Go to **Configuration** → **Data Sources**
   - Verify Prometheus URL and access

3. Time range is appropriate:
   - Check if metrics exist for selected time range
   - Try "Last 5 minutes" to see recent data

### Dashboards Not Loading

**Solutions:**
1. Verify JSON syntax is valid
2. Check Grafana logs: `/var/log/grafana/grafana.log`
3. Ensure provisioning directory has correct permissions
4. Restart Grafana after adding dashboards

### Metrics Missing

Some metrics only appear after certain events:
- Cache metrics: After first query
- Circuit breaker: After webhook failures
- RBAC metrics: After permission checks

## 📚 Resources

- [Grafana Documentation](https://grafana.com/docs/)
- [Prometheus Query Language](https://prometheus.io/docs/prometheus/latest/querying/basics/)
- [Contex Metrics Documentation](../docs/METRICS.md)

## 🤝 Contributing

Improvements welcome! To contribute:

1. Test dashboard with real data
2. Ensure all queries are optimized
3. Add description and usage notes
4. Submit PR with screenshots

## 📄 License

MIT License - Same as Contex project
