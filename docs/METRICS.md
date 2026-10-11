# Prometheus Metrics

Contex exports HTTP metrics at `/metrics`. The endpoint is off (404) unless
`METRICS_TOKEN` is set, and then requires `Authorization: Bearer $METRICS_TOKEN`.

```bash
curl -H "Authorization: Bearer $METRICS_TOKEN" http://localhost:8001/metrics
```

## Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `contex_http_requests_total` | counter | `method`, `endpoint`, `status_code` |
| `contex_http_request_duration_seconds` | histogram | `method`, `endpoint` |
| `contex_active_requests` | gauge | none |

`endpoint` is the matched route template (`/sandbox/projects/{project_id}/data`),
the mount path for mounted apps (`/mcp`, `/static`), or `other` for unmatched
paths, so no label carries a tenant or project ID. MCP tool calls are
`POST /mcp`. `GET /mcp` and `GET /sandbox/subscribe` are long-lived streams, so
leave them out of latency queries.

## Example queries

```promql
# Requests per second by endpoint
sum by (method, endpoint) (rate(contex_http_requests_total[5m]))

# Share of 5xx responses
sum(rate(contex_http_requests_total{status_code=~"5.."}[5m]))
  / sum(rate(contex_http_requests_total[5m]))

# p95 latency of MCP tool calls
histogram_quantile(0.95,
  sum by (le) (rate(contex_http_request_duration_seconds_bucket{method="POST", endpoint="/mcp"}[5m])))
```

## Prometheus and Grafana

[`examples/prometheus.yml`](../examples/prometheus.yml) scrapes Contex with the
token and loads [`examples/prometheus-alerts.yml`](../examples/prometheus-alerts.yml)
(error rate, POST latency, and target down).
[`grafana/contex-dashboard.json`](../grafana/contex-dashboard.json) charts the
metrics above.
