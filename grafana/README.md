# Grafana dashboard

`contex-dashboard.json` charts the HTTP metrics Contex exports (see
[docs/METRICS.md](../docs/METRICS.md)): request rate by endpoint, 4xx and 5xx
responses, POST latency, and in-flight requests.

Import it in Grafana under **Dashboards → New → Import** and pick your
Prometheus data source.
