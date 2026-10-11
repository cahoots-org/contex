# Database Setup Guide

Contex uses ParadeDB (PostgreSQL 18 with `pg_search` + `pgvector`) for all persistent storage, including event sourcing, semantic search embeddings, and application data.

## Overview

**Stack:**
- **ParadeDB (PostgreSQL 18)** - Primary database, bundles `pg_search` (BM25) and `pgvector` extensions
- **pg_search** - BM25 full-text search (ParadeDB extension)
- **pgvector** - Vector similarity search extension (bundled with ParadeDB)
- **SQLAlchemy (async)** - ORM with asyncpg driver
- **Alembic** - Database migrations
- **Postgres `LISTEN/NOTIFY`** - Live subscription updates use channel `contex_subscription_updated`; the listener needs a session-level connection.

## Quick Start

### Docker Compose (Recommended)

`docker compose up -d` starts ParadeDB alongside Contex. Postgres isn't
published to the host; reach it with
`docker compose exec postgres psql -U contex -d contex`.

### Manual Setup

1. **Run ParadeDB** (bundles `pg_search` and `pgvector` — no manual extension build needed):

```bash
docker run -d -p 5432:5432 \
  -e POSTGRES_DB=contex \
  -e POSTGRES_USER=contex \
  -e POSTGRES_PASSWORD=contex_password \
  -v postgres-data:/var/lib/postgresql \
  paradedb/paradedb:0.25.9-pg18
```

Both `pg_search` (BM25) and `pgvector` are bundled in the ParadeDB image and are enabled automatically when you run `alembic upgrade head` (or on first boot via the boot migration). No manual `CREATE EXTENSION` steps are required.

2. **Verify extensions are active**:

```sql
CREATE DATABASE contex;
\c contex

-- Extensions are created by migrations; verify they are loaded:
SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_search');
```

3. **Configure connection**:

```bash
export DATABASE_URL="postgresql+asyncpg://contex:password@localhost:5432/contex"
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://contex:contex_password@localhost:5432/contex` |

The connection pool holds 5 connections and grows to 15 under load, plus one
LISTEN connection for live updates.

### Connection String Format

```
postgresql+asyncpg://username:password@host:port/database
```

Examples:

```bash
# Local development
DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex"

# Docker Compose
DATABASE_URL="postgresql+asyncpg://contex:contex_password@postgres:5432/contex"

# Production with SSL
DATABASE_URL="postgresql+asyncpg://user:pass@db.example.com:5432/contex?ssl=require"
```

## Schema

### Core Tables

**events** - Event sourcing stream (replaces Redis Streams):
```sql
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    project_id VARCHAR(255) NOT NULL,
    tenant_id VARCHAR(255),
    event_type VARCHAR(255) NOT NULL,
    data JSONB NOT NULL,
    sequence BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, sequence)
);
```

**embeddings** - Semantic search with pgvector:
```sql
CREATE TABLE embeddings (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(255) NOT NULL,
    data_key VARCHAR(255) NOT NULL,
    node_key VARCHAR(255) NOT NULL,
    description TEXT,
    data JSONB NOT NULL,
    embedding vector(384) NOT NULL,  -- Sentence transformer dimension
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, node_key)
);

-- HNSW index for fast similarity search
CREATE INDEX idx_embeddings_vector ON embeddings
    USING hnsw (embedding vector_cosine_ops);
```

**agent_registrations** - Agent subscription data:
```sql
CREATE TABLE agent_registrations (
    agent_id VARCHAR(255) PRIMARY KEY,
    project_id VARCHAR(255) NOT NULL,
    needs TEXT[] NOT NULL DEFAULT '{}',
    notification_method VARCHAR(20) NOT NULL DEFAULT 'redis',
    notification_channel VARCHAR(255),
    webhook_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Authentication Tables

**api_keys** - API key storage:
```sql
CREATE TABLE api_keys (
    key_id VARCHAR(255) PRIMARY KEY,
    key_hash VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    prefix VARCHAR(10) NOT NULL,
    scopes TEXT[] NOT NULL DEFAULT '{}',
    tenant_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**api_key_roles** - RBAC roles:
```sql
CREATE TABLE api_key_roles (
    key_id VARCHAR(255) PRIMARY KEY REFERENCES api_keys(key_id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL DEFAULT 'readonly',
    projects TEXT[] NOT NULL DEFAULT '{}'
);
```

### Multi-Tenancy Tables

**tenants** - Tenant management:
```sql
CREATE TABLE tenants (
    tenant_id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    plan VARCHAR(50) NOT NULL DEFAULT 'free',
    quotas JSONB NOT NULL DEFAULT '{}',
    settings JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## Migrations

Contex uses Alembic for database migrations:

```bash
# Generate a new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# View migration history
alembic history
```

## Vector Search

### pgvector Similarity Search

Contex uses cosine similarity for semantic matching:

```sql
-- Find similar embeddings
SELECT node_key, data,
       1 - (embedding <=> query_vector) AS similarity
FROM embeddings
WHERE project_id = 'my-project'
  AND 1 - (embedding <=> query_vector) > 0.5  -- threshold
ORDER BY embedding <=> query_vector
LIMIT 10;
```

### Index Types

- **HNSW** (default): Fast approximate nearest neighbor, good for most cases
- **IVFFlat**: Better for very large datasets, requires training

```sql
-- HNSW index (recommended)
CREATE INDEX ON embeddings USING hnsw (embedding vector_cosine_ops);

-- IVFFlat index (for 1M+ vectors)
CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

## Backup & Recovery

### PostgreSQL Native Backup

```bash
# Full backup
pg_dump -h localhost -U contex contex > backup.sql

# With compression
pg_dump -h localhost -U contex -Fc contex > backup.dump

# Restore
pg_restore -h localhost -U contex -d contex backup.dump
```

### Docker Volume Backup

```bash
# Backup volume
docker run --rm -v contex_postgres-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres-backup.tar.gz /data

# Restore volume
docker run --rm -v contex_postgres-data:/data -v $(pwd):/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/postgres-backup.tar.gz -C /"
```

### Point-in-Time Recovery

For production, configure WAL archiving:

```bash
# postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'cp %p /archive/%f'
```

## Performance Tuning

### PostgreSQL Configuration

For production workloads:

```ini
# postgresql.conf
shared_buffers = 256MB
effective_cache_size = 1GB
work_mem = 64MB
maintenance_work_mem = 128MB
max_connections = 100
```

### Vacuum and Analyze

Keep statistics updated:

```sql
-- Manual vacuum and analyze
VACUUM ANALYZE embeddings;
VACUUM ANALYZE events;

-- Configure autovacuum
ALTER TABLE events SET (autovacuum_vacuum_scale_factor = 0.1);
```

## Monitoring

### Health Check

```bash
# Liveness probe (unauthenticated; returns only a liveness signal)
curl http://localhost:8001/health

# Response
{
  "status": "healthy"
}
```

### pg_stat Queries

```sql
-- Active connections
SELECT count(*) FROM pg_stat_activity WHERE datname = 'contex';

-- Table sizes
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- Slow queries
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
```

## Troubleshooting

### Connection Issues

```bash
# Test connection
psql "postgresql://contex:password@localhost:5432/contex"

# Check logs
docker logs contex-postgres-1

# Verify pgvector extension
psql -c "SELECT * FROM pg_extension WHERE extname = 'vector';"
```

### Migration Issues

```bash
# Check current revision
alembic current

# View pending migrations
alembic history --verbose

# Reset to clean state (development only!)
alembic downgrade base
alembic upgrade head
```

### Performance Issues

```sql
-- Check for missing indexes
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0;

-- Check for table bloat
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename))
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC;
```

## Related Documentation

- [Metrics](METRICS.md) - Monitoring and observability
