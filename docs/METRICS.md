# Prometheus Metrics

Contex exports HTTP and MCP metrics at `/metrics`. The endpoint is off (404) unless
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

| `contex_mcp_tool_calls_total` | counter | `tool`, `outcome` |
| `contex_mcp_tool_duration_seconds` | histogram | `tool` |
| `contex_mcp_listen_streams` | gauge | none |
| `contex_mcp_resource_updates_total` | counter | none |

`endpoint` is the matched route template (`/sandbox/data`),
the mount path for mounted apps (`/mcp`, `/static`), or `other` for unmatched
paths, so no label carries a tenant or project ID. All MCP traffic is
`endpoint="/mcp"`, and tool failures come back inside a 200 response, so use
the `contex_mcp_*` metrics for MCP.

`tool` is the tool name, or `unknown` for a name that isn't a Contex tool.
`outcome` is `ok` or `error`; `error` covers caller mistakes (invalid
arguments, permission denied) as well as server failures, so check the ERROR
logs before treating a rise as an outage. `contex_mcp_listen_streams` counts
open `subscriptions/listen` streams; 2025-protocol clients that push through
`resources/subscribe` are not included. `contex_mcp_resource_updates_total`
counts `resources/updated` notifications published to subscribers.

## Example queries

```promql
# Requests per second by endpoint
sum by (method, endpoint) (rate(contex_http_requests_total[5m]))

# Share of 5xx responses
sum(rate(contex_http_requests_total{status_code=~"5.."}[5m]))
  / sum(rate(contex_http_requests_total[5m]))

# MCP tool calls per second by tool and outcome
sum by (tool, outcome) (rate(contex_mcp_tool_calls_total[5m]))

# p95 MCP tool latency by tool
histogram_quantile(0.95, sum by (tool, le) (rate(contex_mcp_tool_duration_seconds_bucket[5m])))
```

## Prometheus and Grafana

[`examples/prometheus.yml`](../examples/prometheus.yml) scrapes Contex with the
token and loads [`examples/prometheus-alerts.yml`](../examples/prometheus-alerts.yml)
(HTTP error rate and target down).
[`grafana/contex-dashboard.json`](../grafana/contex-dashboard.json) charts the
metrics above.
