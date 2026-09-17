# Contex Examples

Runnable examples that drive Contex over MCP, the same interface your agents use.

## Prerequisites

Start Contex:

```bash
docker compose up
```

Install the MCP client SDK the examples use. The scripts target `mcp` v2 (the
version the server pins); the v1 client API differs.

```bash
pip install "mcp>=2,<3"
```

Both scripts connect to `http://localhost:8001/mcp` by default. Point them at
another server with `CONTEX_MCP_URL`, e.g.
`CONTEX_MCP_URL=http://localhost:8011/mcp`.

## Examples

### [`mcp_quickstart.py`](mcp_quickstart.py)

The full loop. A producer publishes a database DSN under an arbitrary key, an
agent subscribes to `"how the service reaches its datastore"` (which shares no
keywords with the data), reads the matched context, and reads it again after the
data changes without issuing a second query.

```bash
python examples/mcp_quickstart.py
```

### [`query.py`](query.py)

One-shot semantic query with no subscription. Publishes a few operational facts,
then asks a plain-English question and prints the matches ranked by meaning.

```bash
python examples/query.py
```

## Also here

`prometheus.yml` and `prometheus-alerts.yml` are sample scrape and alerting
configs referenced by [the metrics guide](../docs/METRICS.md). They are Prometheus
configuration, not Contex clients.
