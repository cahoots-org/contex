<p align="center">
  <img src="docs/assets/social-card.png" alt="Contex: the context bus for agents" width="820">
</p>

<p align="center"><b>Agents subscribe to the context they need, and Contex keeps it current as your data changes.</b></p>

<p align="center">
  <a href="https://github.com/cahoots-org/contex/actions/workflows/ci.yml"><img src="https://github.com/cahoots-org/contex/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>

Your agent's context lives in systems that keep changing: databases, tickets, docs, other agents. Keeping it correct as they change is the hard part. Re-fetch on every turn and you pay for the round trip every time. Fetch once and hold onto it, and you have to know when it went stale and refresh it, or the agent acts on outdated data.

With Contex, an agent subscribes once to what it needs, in plain English. When you publish a change, the affected subscriptions re-match and refresh. The agent reads current context on its next turn, with no fetch to run, no cache to hold, and nothing to invalidate.

## How it works

Your app or agents publish updates as things change, in any format and with no schema. An agent subscribes with a need in plain English, and Contex matches it to the data by meaning, even when they share no words. As new updates arrive, the subscription refreshes, so the agent reads current context instead of re-querying. Publishing and subscribing are both MCP tools (`contex_publish` and `contex_create_subscription`), so this works from any MCP client, including Claude and Cursor.

For example, several agents work one codebase, each publishing what it changes. An agent adding a refund flow subscribes to *"recent changes to the payment code,"* sees that another agent changed `charge()` to take a currency argument, and calls the new signature instead of breaking on the old one.

## Quickstart

Contex runs over MCP with auth off by default, so there's nothing to configure.

**1. Start Contex.** The first run builds the image and downloads the embedding model, so give it a minute.

```bash
git clone https://github.com/cahoots-org/contex.git
cd contex
docker compose up
```

It serves on `http://localhost:8001`, with the MCP endpoint at `/mcp`.

**2. Connect your MCP client.** For Claude Code:

```bash
claude mcp add --transport http contex http://localhost:8001/mcp
```

Any MCP client works. Point it at `http://localhost:8001/mcp`.

**3. Publish, subscribe, and watch it stay current.** Call the tools from your client or agent:

```jsonc
// Publish a change (no schema, you pick the key)
contex_publish {
  "project_id": "quickstart",
  "data_key": "change:charge",
  "data": { "file": "payments/charge.py", "note": "charge() now requires a currency argument" }
}

// Subscribe to a need in plain English (no words shared with the data)
contex_create_subscription {
  "project_id": "quickstart",
  "needs": ["recent changes to the payment code"]
}
// returns: { "subscription_id": "sub_...", "resource_uri": "contex://subscriptions/sub_..." }
```

Read the `contex://subscriptions/sub_...` resource and you get the change back, matched by meaning even though the need and the data share no words. Publish another change and read again. The subscription already reflects it, and your agent never issued a query.

Prefer to watch it happen? Open `http://localhost:8001/sandbox`, type a need, and hit **Watch**.

> Production uses `AUTH_ENABLED=true`. The quickstart runs in open dev mode. See [Security](#security).

## MCP tools

| Tool | Purpose |
|------|---------|
| `contex_publish` | Publishes or updates a project's data (any format, no schema). |
| `contex_publish_batch` | Publishes or updates many items in one call. |
| `contex_query` | Searches a project's context once, without subscribing. |
| `contex_create_subscription` | Creates a live subscription from plain-English needs. Returns a `contex://subscriptions/{id}` resource. |
| `contex_delete_subscription` | Deletes a subscription. |

**Resource:** `contex://subscriptions/{id}` holds a subscription's matched context. Contex re-matches it when the underlying data changes, so every read is up to date.

## Features

- **Semantic matching:** matches needs to data by meaning, using sentence-transformer embeddings.
- **Hybrid search:** combines vector similarity and BM25 keyword matching.
- **Live subscriptions:** matched context that refreshes when your data changes.
- **Schema-free:** publish JSON, YAML, CSV, XML, or plain text.
- **Event sourcing:** stores every change as an immutable event, for audit trails and point-in-time queries. See [Event Sourcing](docs/EVENT_SOURCING.md).
- **Security:** API-key auth and RBAC, off by default for local dev.
- **Multi-tenancy:** always-on tenant isolation. A default tenant when auth is off, full per-identity isolation when it's on.
- **Observability:** structured logging, Prometheus metrics, and OpenTelemetry tracing.

## Connectors

Connectors load an existing source into a Contex project without any glue code. Each is a separate program that reads from the source and publishes over MCP.

| Connector | Loads |
|-----------|-------|
| **Postgres** | Rows from the tables you choose. Table and column allow-lists. Binary columns skipped. |
| **S3** | Text objects under a bucket or prefix. Also MinIO, Cloudflare R2, and LocalStack via `endpoint_url`. |
| **GitHub** | Files, issues, and pull requests from one or more repos. |

```bash
pip install -r connectors/postgres/requirements.txt
python -m connectors.postgres --config connector.yaml
```

The connectors today are bulk snapshots: a run reads the source and upserts each item, so re-running (say, on a cron) refreshes the data.

### Write your own

A connector reads from any source and publishes over the same MCP tools, so writing one is a reader that yields `ChangeEvent`s:

```python
from connectors.base import ChangeEvent, ContexConfig, run_connector

def read(source):
    for item in source:                 # your source: an API, a file, a queue
        yield ChangeEvent(op="upsert", key=item["id"], payload=item)

await run_connector(
    ContexConfig(url="http://localhost:8001/mcp", project_id="my-app"),
    read(my_source),
)
```

See [connectors/README.md](connectors/README.md) and each connector's `connector.yaml.example`.

## Security

Auth is **off by default**, so local development needs no keys. To turn it on, copy `.env.example` to `.env` and set:

```bash
AUTH_ENABLED=true
SERVICE_ACCOUNT_JWT_SECRET=    # required, or the server won't boot
API_KEY_PEPPER=                # recommended, at least 16 characters
```

`docker compose up` reads `.env` automatically.

With auth on, Contex authenticates every MCP tool call, scopes each request to the caller's tenant, and applies RBAC to who can publish, query, and subscribe. Connectors and other services authenticate as service accounts.

See the [RBAC Guide](docs/RBAC.md).

## Roadmap

- **More connectors:** streaming sources like Redis, Kafka, and message queues that push updates as they happen, plus more batch sources like Slack and Atlassian.
- **Document extraction:** read text from PDF and DOCX files.
- **Higher throughput:** for high-volume sources.

## Development

```bash
git clone https://github.com/cahoots-org/contex.git
cd contex
docker compose up -d      # Contex, ParadeDB, Redis
pytest tests/ -v
```

Each connector has its own tests and dependencies. Run one with:

```bash
pip install -r connectors/s3/requirements-dev.txt
pytest connectors/s3 -v
```

Contex runs on **ParadeDB** (`paradedb/paradedb`), which bundles `pg_search` (BM25) and `pgvector` (embeddings) in one image. Stock Postgres images won't work. See [Database Setup](docs/DATABASE.md).

## Documentation

- **[Connectors](connectors/README.md):** bulk-load Postgres, S3, and GitHub, or write your own
- **[Database Setup](docs/DATABASE.md):** ParadeDB (pg_search + pgvector) configuration
- **[Event Sourcing](docs/EVENT_SOURCING.md):** point-in-time queries and audit trails
- **[RBAC](docs/RBAC.md):** role-based access control
- **[Metrics](docs/METRICS.md):** Prometheus metrics
- **[Logging](docs/LOGGING.md):** structured logging
- **[Operational Runbooks](docs/RUNBOOKS.md):** incident response
- **[Contributing](CONTRIBUTING.md)**
- **[.env.example](.env.example):** every environment variable, documented

## License

MIT License. See [LICENSE](LICENSE).

## Links

- **[GitHub](https://github.com/cahoots-org/contex)**
- **[Benchmarks (contex-eval)](https://github.com/cahoots-org/contex-eval)**
- **[Issues](https://github.com/cahoots-org/contex/issues)**
