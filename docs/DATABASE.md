# Database Setup Guide

Contex uses ParadeDB (PostgreSQL 18 with `pg_search` + `pgvector`) for all persistent storage, including semantic search embeddings and application data.

## Overview

**Stack:**
- **ParadeDB (PostgreSQL 18)** - Primary database, bundles `pg_search` (BM25) and `pgvector` extensions
- **pg_search** - BM25 full-text search (ParadeDB extension)
- **pgvector** - Vector similarity search extension (bundled with ParadeDB)
- **SQLAlchemy (async)** - ORM with asyncpg driver
- **Alembic** - Database migrations
- **Postgres `LISTEN/NOTIFY`** - Live subscription updates use channel `contex_subscription_updated`; the listener needs a session-level connection.

## Quick Start

`docker compose up -d` starts ParadeDB alongside Contex. Postgres isn't
published to the host; reach it with
`docker compose exec postgres psql -U contex -d contex`.

To run Contex outside Docker against a standalone ParadeDB container, follow
[CONTRIBUTING](../CONTRIBUTING.md).

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://contex:contex_password@localhost:5432/contex` |
| `DB_POOL_SIZE` | Connections the pool keeps open | `5` |
| `DB_MAX_OVERFLOW` | Extra connections opened under load | `10` |
| `DB_POOL_TIMEOUT` | Seconds a request waits for a connection | `30` |

By default the pool holds 5 connections and grows to 15 under load, plus one
LISTEN connection for live updates. Subscription reconciles take at most two
connections each, `RECONCILE_CONCURRENCY` (default 2) at a time, so requests
always have the rest.

The format is `postgresql+asyncpg://username:password@host:port/database`. Add
`?ssl=require` for a database that needs TLS.

## Schema and migrations

The migrations in `alembic/versions/` are the schema. Contex applies them at
boot, creating the extensions on the first run. To work with them by hand:

```bash
alembic current               # current revision
alembic history               # all revisions
alembic upgrade head          # apply pending migrations
alembic revision -m "change"  # new migration
```

## Backup and restore

See [RUNBOOKS](RUNBOOKS.md#backup-and-restore).

## Related Documentation

- [Metrics](METRICS.md) - Monitoring and observability
