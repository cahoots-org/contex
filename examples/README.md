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

The scripts connect to `http://localhost:8001/mcp` by default. Point them at
another server with `CONTEX_MCP_URL`, e.g.
`CONTEX_MCP_URL=http://localhost:8011/mcp`.

## Examples

### [`mcp_quickstart.py`](mcp_quickstart.py)

The full loop. A producer publishes a database DSN under an arbitrary key, an
agent subscribes to `"how the service reaches its datastore"` (which shares no
keywords with the data), and reads the matched context.

```bash
python examples/mcp_quickstart.py
```

### [`mcp_watch.py`](mcp_watch.py)

The push. An agent creates a subscription and listens on its resource with
`subscriptions/listen`. A second client publishes a change, and the server pushes
an update to the agent, which re-reads the resource. No polling, no re-query.

```bash
python examples/mcp_watch.py
```

### [`query.py`](query.py)

One-shot semantic query with no subscription. Publishes a few operational facts,
then asks a plain-English question and prints the matches ranked by meaning.

```bash
python examples/query.py
```

### [`whispers/`](whispers/README.md)

A gossip game with 50 villagers, played in the terminal or a browser. Each villager's mind is a project
with one subscription on their needs, they wake only on push, and they forget
through `max_age_seconds`. You whisper rumors and watch them spread and change
by meaning. Needs `ANTHROPIC_API_KEY` and `TYPESAFE_API_KEY`. An
optional faster reconcile sweep makes quiet minds forget sooner; see its README
for setup and cost.

```bash
pip install -r examples/whispers/requirements.txt
python -m examples.whispers          # terminal
python -m examples.whispers --web    # browser, at http://127.0.0.1:8765
```

## Also here

`prometheus.yml` and `prometheus-alerts.yml` are sample scrape and alerting
configs referenced by [the metrics guide](../docs/METRICS.md). They are Prometheus
configuration, not Contex clients.
