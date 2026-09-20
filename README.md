<p align="center">
  <img src="docs/assets/social-card.png" alt="Contex — an MCP-native context bus" width="820">
</p>

<p align="center"><b>An MCP-native context bus. Your agents declare what they need, and the context comes to them.</b></p>

<p align="center">
  <a href="https://github.com/cahoots-org/contex/actions/workflows/ci.yml"><img src="https://github.com/cahoots-org/contex/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"></a>
</p>

Agents shouldn't have to fetch their own context. With Contex, an agent declares what it needs in plain English, and Contex assembles the right context and keeps it current. The agent never formulates a query, calls a search tool, or looks anything up.

Today an agent fetches context itself. It decides what to search for, calls the tool, and stitches the results into its reasoning, turn after turn. And it only ever finds what it already knew to ask for. Contex flips that: the agent subscribes to a need once, and Contex delivers the relevant context and keeps it current as your data changes.

**Context that stays current on its own.** The agent reads its context instead of searching for it every turn, and Contex keeps that context correct as your data changes. When something you publish moves, Contex re-checks the subscriptions it affects and refreshes them. The agent spends no turns re-searching to stay current, and it never runs on stale context.

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
| `contex_publish_batch` | Publishes or updates many items in one call (used by connectors). |
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
- **Security:** API-key auth and RBAC, off by default for local dev.
- **Multi-tenancy:** always-on tenant isolation. The default tenant applies when `AUTH_ENABLED=false`, and full identity-derived isolation activates under `AUTH_ENABLED=true`.
- **Observability:** structured logging, Prometheus metrics, and OpenTelemetry tracing.

## Connectors

Connectors bulk-load an existing source into a Contex project with no glue code. Each is an out-of-process CLI: it reads the source read-only and publishes over MCP as a service account.

| Connector | Loads |
|-----------|-------|
| **Postgres** | Rows from the tables you point it at — table/column allow-lists, binary columns skipped. |
| **S3** | Text-like objects under a bucket/prefix; also MinIO, Cloudflare R2, and LocalStack via `endpoint_url`. |
| **GitHub** | Files, issues, and pull requests from one or more repos. |

```bash
pip install -r connectors/postgres/requirements.txt
python -m connectors.postgres --config connector.yaml
```

v1 connectors are **bulk snapshots**: a run reads the source and upserts every item by a stable key, so re-running (e.g. on a cron) is the refresh story. They are not live sync, and deletes don't propagate. See [connectors/README.md](connectors/README.md) and each connector's `connector.yaml.example`.

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
```

With `AUTH_ENABLED=true`, Contex authenticates every MCP tool call, scopes every request to the caller's tenant (tenant isolation activates automatically), and applies RBAC to who can publish, query, and subscribe. Connectors and other services authenticate as service accounts.

See the [RBAC Guide](docs/RBAC.md).

## Roadmap

[Connectors](#connectors) bulk-load Postgres, S3, and GitHub today, alongside the publish tools. Next, ingestion gets broader and more real-time:

- **More connectors + an SDK:** more first-party sources (Slack, Atlassian) and a framework to build your own for niche ones.
- **Live sync:** move beyond bulk snapshots to incremental/CDC updates and delete propagation.
- **Document extraction:** pull text from PDF and DOCX (and OCR) instead of skipping them.
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

Connectors have their own suites and dependencies; run one with, e.g.:

```bash
pip install -r connectors/s3/requirements-dev.txt
pytest connectors/s3 -v
```

Contex requires **ParadeDB** (`paradedb/paradedb`) as its database. It bundles `pg_search` (BM25) and `pgvector` (embeddings) in one Postgres-compatible image. Contex does not support stock Postgres images. See [Database Setup](docs/DATABASE.md). Railway deployments should use the ParadeDB template.

## Documentation

- **[Connectors](connectors/README.md):** bulk-load Postgres, S3, and GitHub into a project
- **[Database Setup](docs/DATABASE.md):** ParadeDB (pg_search + pgvector) configuration
- **[Event Sourcing](docs/EVENT_SOURCING.md):** time-travel queries and compliance
- **[RBAC](docs/RBAC.md):** role-based access control
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
