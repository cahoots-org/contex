# Contex

**An MCP-native context bus. Your agents declare what they need, and the context comes to them.**

[![CI](https://github.com/cahoots-org/contex/actions/workflows/ci.yml/badge.svg)](https://github.com/cahoots-org/contex/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Agents shouldn't have to fetch their own context. With Contex, an agent declares what it needs in plain English, and Contex assembles the right context and keeps it current. The agent never formulates a query, calls a search tool, or looks anything up.

Today an agent fetches context itself. It decides what to search for, calls the tool, and stitches the results into its reasoning, turn after turn. And it only ever finds what it already knew to ask for. Contex flips that: the agent subscribes to a need once, and Contex delivers the relevant context and keeps it current as your data changes.

## How it works

A producer **publishes** data to Contex in any format (JSON, text, and more), with no schema. An agent **subscribes** by describing a need in plain English. Contex matches the data to the need by meaning and gives the agent a subscription that always holds the current context relevant to that need. When the underlying data changes, the subscription updates itself. The agent re-reads it but never re-queries.

Both sides are MCP tools. Producers call `contex_publish`. Agents call `contex_create_subscription`, then read a live context resource. Publish a database DSN under any key, subscribe to *"how the service reaches its datastore,"* and Contex matches them by meaning even though they share no keywords.

Your app can publish its state as it changes, and your agent subscribes to the slice it needs. The same mechanic lets one agent publish what it learns while another subscribes to it, with no coordination code between them.

*Independently benchmarked on two public datasets, HotpotQA and SciFact: Contex's hybrid retrieval outperforms both pure dense and BM25 retrieval, with statistically significant margins. [See the results](https://github.com/cahoots-org/contex-eval).*

## Quickstart

Contex runs over MCP. This starts the server and connects it to your MCP client. Auth is off by default, so there's nothing to configure.

**1. Start Contex and its database.** The first run builds the image and downloads the embedding model, so give it a minute:

```bash
git clone https://github.com/cahoots-org/contex.git
cd contex
docker compose up
```

Contex serves on `http://localhost:8001`, with the MCP endpoint at `/mcp`.

**2. Connect your MCP client.** For Claude Code:

```bash
claude mcp add --transport http contex http://localhost:8001/mcp
```

Any MCP client works. Point it at `http://localhost:8001/mcp`.

**3. Publish, subscribe, and watch it stay current.** From your MCP client or your agent, call the tools:

```jsonc
// Publish some data (no schema, any keys)
contex_publish {
  "project_id": "quickstart",
  "data_key": "pg_dsn",
  "data": { "engine": "postgres", "host": "db.internal", "port": 5432, "pool": 20 }
}

// Subscribe to a need in plain English (no words shared with the data)
contex_create_subscription {
  "project_id": "quickstart",
  "needs": ["how the service reaches its datastore"]
}
// returns: { "subscription_id": "sub_...", "resource_uri": "contex://subscriptions/sub_..." }
```

Read the `contex://subscriptions/sub_...` resource to get the matched context. Contex matched it by meaning: the need and the data share no keywords. Publish a change to `pg_dsn` and read the resource again. The subscription is already current, and your agent never issued a query.

Prefer to watch it happen? Open `http://localhost:8001/sandbox`, type a need, and hit **Watch**.

> Production uses `AUTH_ENABLED=true`. The quickstart runs in the default open dev mode. See [Security](#security).

## MCP tools

| Tool | Purpose |
|------|---------|
| `contex_publish` | Publishes or updates context data for a project (schema-free). |
| `contex_query` | Runs a one-shot semantic query over a project's context (stateless). |
| `contex_create_subscription` | Creates a live subscription from plain-English needs and returns a `contex://subscriptions/{id}` resource URI. |
| `contex_delete_subscription` | Deletes a subscription. |

**Resource:** `contex://subscriptions/{id}` holds a subscription's current matched context. Contex re-matches it whenever the underlying data changes, so a read always reflects the latest state.

## Features

- **Semantic matching:** matches needs to data by meaning, using sentence-transformer embeddings.
- **Hybrid search:** fuses pgvector similarity with `pg_search` BM25 lexical ranking through Reciprocal Rank Fusion (RRF).
- **Live subscriptions:** materialized, bounded context bundles that re-match themselves when data changes.
- **Schema-free:** publish JSON, YAML, CSV, XML, or plain text.
- **Event sourcing:** stores every change as an immutable event for audit trails and time-travel. See [Event Sourcing](docs/EVENT_SOURCING.md).
- **Security:** API-key auth, RBAC, and rate limiting, off by default for local dev. See [Security](docs/SECURITY.md).
- **Multi-tenancy:** always-on tenant isolation. The default tenant applies when `AUTH_ENABLED=false`, and full identity-derived isolation activates under `AUTH_ENABLED=true`.
- **Observability:** structured logging, Prometheus metrics, and OpenTelemetry tracing.

## Hybrid search

Contex fuses two retrievers. Semantic vector search (pgvector over sentence-transformer embeddings) handles meaning, and BM25 lexical search (`pg_search`) handles exact terms like identifiers, config keys, and error codes. Reciprocal Rank Fusion merges the two rankings, so a subscription surfaces both semantically related context and exact keyword hits.

Both extensions ship in the ParadeDB image, so hybrid search runs entirely inside Postgres, with no separate search service. Two settings control it: `HYBRID_SEARCH_ENABLED` (on in the default compose) and `RRF_K`. For the independent benchmark comparing it to pure dense and BM25, see [contex-eval](https://github.com/cahoots-org/contex-eval).

## Security

Auth is **off by default** so local development needs no keys. For production, enable it:

```bash
export AUTH_ENABLED=true

# Required when AUTH_ENABLED=true: the server refuses to boot without it
export SERVICE_ACCOUNT_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Recommended: pepper for API-key hashing (defense in depth)
export API_KEY_PEPPER=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Optional: rate limiting
export RATE_LIMIT_ENABLED=true
export RATE_LIMIT_REQUESTS=100  # per minute
```

With `AUTH_ENABLED=true`, Contex authenticates every MCP tool call, scopes every request to the caller's tenant (tenant isolation activates automatically), and applies RBAC to who can publish, query, and subscribe. Connectors and other services authenticate as service accounts.

See the [Security Overview](docs/SECURITY.md) and [RBAC Guide](docs/RBAC.md).

## Roadmap

Today, data enters Contex through the publish tool. Your services and agents push what they know. The next major step closes the loop on ingestion so data arrives without glue code:

- **Connectors:** independently-scalable connector processes that ingest from common sources (GitHub, Slack, Postgres/CDC, Kafka and Redis streams) and publish into Contex over MCP, built on a generic change-event core so new sources are cheap to add.
- **Connector SDK:** build your own connectors for niche sources.
- **Hardened ingestion:** throughput, idempotency, and incremental reconcile for high-volume streams.

## Development

```bash
git clone https://github.com/cahoots-org/contex.git
cd contex

# Start services (Contex, ParadeDB, Redis)
docker compose up -d

# Run the test suite
pytest tests/ -v
```

Contex requires **ParadeDB** (`paradedb/paradedb`) as its database. It bundles `pg_search` (BM25) and `pgvector` (embeddings) in one Postgres-compatible image. Contex does not support stock Postgres images. See [Database Setup](docs/DATABASE.md). Railway deployments should use the ParadeDB template.

## Documentation

- **[Security](docs/SECURITY.md):** authentication, RBAC, rate limiting
- **[Database Setup](docs/DATABASE.md):** ParadeDB (pg_search + pgvector) configuration
- **[Event Sourcing](docs/EVENT_SOURCING.md):** time-travel queries and compliance
- **[RBAC](docs/RBAC.md):** role-based access control
- **[Rate Limiting](docs/RATE_LIMITING.md):** protection and limits
- **[Metrics](docs/METRICS.md):** Prometheus metrics and monitoring
- **[Logging](docs/LOGGING.md):** structured logging and observability
- **[Operational Runbooks](docs/RUNBOOKS.md):** incident response and operations
- **[Contributing](CONTRIBUTING.md)**
- **[.env.example](.env.example):** all environment variables documented

## License

MIT License. See [LICENSE](LICENSE).

## Links

- **[GitHub](https://github.com/cahoots-org/contex)**
- **[Benchmarks (contex-eval)](https://github.com/cahoots-org/contex-eval)**
- **[Issues](https://github.com/cahoots-org/contex/issues)**
