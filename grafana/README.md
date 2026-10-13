# Grafana dashboard

`contex-dashboard.json` charts the metrics Contex exports (see
[docs/METRICS.md](../docs/METRICS.md)): HTTP request rate by endpoint, 4xx and
5xx responses and in-flight requests; MCP tool calls, p95 tool latency, open
listen streams and pushed updates.

Import it in Grafana under **Dashboards → New → Import** and pick your
Prometheus data source.
