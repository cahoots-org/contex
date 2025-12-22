# Contex

**Semantic context routing for AI agent systems**

[![Tests](https://img.shields.io/badge/tests-311%20passing-success)](tests/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Contex delivers relevant project context to AI agents using semantic matching. Agents describe their needs in natural language, and Contex automatically routes matching data with real-time updates—no schemas, no polling.

## Features

- **Semantic Matching** - AI-powered filtering using sentence transformers
- **Vector Search** - PostgreSQL + pgvector for efficient semantic similarity search
- **Hybrid Search** - Combines BM25 lexical search with semantic vector search for better accuracy
- **Real-time Updates** - Redis pub/sub or webhooks for instant notifications
- **Schema-Free** - Publish JSON, YAML, CSV, XML, or plain text
- **Event Sourcing** - Complete audit trail for time-travel queries and compliance
- **Data Management** - Automatic retention policies, export/import, and backup
- **Security** - API key auth, RBAC, rate limiting, and security headers
- **Observability** - Structured logging, Prometheus metrics, and distributed tracing
- **Multi-Tenancy** - Isolated tenants with project-level permissions and quotas
- **Sandbox UI** - Interactive web interface for testing
- **API Versioning** - Stable /api/v1 endpoints with backward compatibility

---

## Quick Start

### 1. Install the Python SDK

```bash
pip install contex-python
```

### 2. Start Contex Server

Create a `docker-compose.yml`:

```yaml
services:
  contex:
    image: ghcr.io/cahoots-org/contex:latest
    ports:
      - "8001:8001"
    environment:
      - DATABASE_URL=postgresql+asyncpg://contex:contex_password@postgres:5432/contex
      - REDIS_URL=redis://redis:6379
      - OPENSEARCH_URL=http://opensearch:9200
      - SIMILARITY_THRESHOLD=0.5
      - MAX_MATCHES=10
      - MAX_CONTEXT_SIZE=51200
      - HYBRID_SEARCH_ENABLED=true  # Enable BM25 + semantic hybrid search
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      opensearch:
        condition: service_healthy

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      - POSTGRES_DB=contex
      - POSTGRES_USER=contex
      - POSTGRES_PASSWORD=contex_password
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U contex -d contex"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine  # Lightweight - only used for pub/sub
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  opensearch:
    image: opensearchproject/opensearch:2.11.0
    environment:
      - discovery.type=single-node
      - plugins.security.disabled=true
      - "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m"
    ports:
      - "9200:9200"
    volumes:
      - opensearch-data:/usr/share/opensearch/data
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:9200/_cluster/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres-data:
  opensearch-data:
```

Then run:
```bash
docker compose up -d
```

**🎨 Sandbox UI:** Open http://localhost:8001

### 3. Use the SDK

```python
from contex import ContexAsyncClient

async with ContexAsyncClient(url="http://localhost:8001") as client:
    # Publish data
    await client.publish(
        project_id="my-app",
        data_key="api_config",
        data={"base_url": "https://api.example.com", "timeout": 30}
    )

    # Register agent
    response = await client.register_agent(
        agent_id="my-agent",
        project_id="my-app",
        data_needs=["API configuration and endpoints"]
    )

    # Agent receives registration response with matched needs
    print(f"Matched needs: {response.matched_needs}")
    print(f"Notification channel: {response.notification_channel}")
```

**[Python SDK Documentation](sdk/python/README.md)** | **[PyPI Package](https://pypi.org/project/contex-python/)**

---

## How It Works

### 1. Publish Data

Publish any format—JSON, YAML, TOML, or plain text:

```python
# JSON data
await client.publish(
    project_id="my-app",
    data_key="api_config",
    data={"base_url": "https://api.example.com", "timeout": 30}
)

# YAML configuration
await client.publish(
    project_id="my-app",
    data_key="infra_config",
    data="database:\n  host: localhost\n  port: 5432",
    data_format="yaml"
)
```

### 2. Register Agents

Agents describe their needs in natural language:

```python
response = await client.register_agent(
    agent_id="code-reviewer",
    project_id="my-app",
    data_needs=[
        "code style guidelines and linting rules",
        "testing requirements and coverage goals"
    ]
)

# Contex returns registration with matched needs count
print(f"Matched needs: {response.matched_needs}")
```

### 3. Receive Updates

Choose Redis pub/sub or webhooks:

```python
# Option A: Redis pub/sub
import redis.asyncio as redis

r = await redis.from_url("redis://localhost:6379")
pubsub = r.pubsub()
await pubsub.subscribe("agent:code-reviewer:updates")

async for message in pubsub.listen():
    if message["type"] == "message":
        update = json.loads(message["data"])
        # Process updated context

# Option B: Webhooks
response = await client.register_agent(
    agent_id="code-reviewer",
    project_id="my-app",
    data_needs=["code style guidelines"],
    notification_method="webhook",
    webhook_url="https://my-agent.com/webhook"
)
```

---

## Security

Production-ready security with API keys, RBAC, and rate limiting:

```python
# Use API key for authentication
async with ContexAsyncClient(
    url="http://localhost:8001",
    api_key="ck_your_api_key_here"
) as client:
    await client.publish(...)

# Create and manage keys
key_response = await client.create_api_key(name="backend-service")
await client.assign_role(
    key_id=key_response.key_id,
    role="publisher",
    projects=["my-app"]
)
```

**Features:**
- 🔐 API key authentication (ck_ prefix)
- 👥 RBAC with 4 roles (admin, publisher, consumer, readonly)
- ⏱️ Rate limiting (100-200 req/min per key)
- 🔒 Project-level permissions

**Production Configuration:**
```bash
# Enable authentication (disabled by default for easy development)
export AUTH_ENABLED=true

# REQUIRED when AUTH_ENABLED=true: Set API key salt for secure hashing
export API_KEY_SALT=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Optional: Configure rate limiting
export RATE_LIMIT_ENABLED=true
export RATE_LIMIT_REQUESTS=100  # requests per minute
```

> ⚠️ **Note:** Authentication is **opt-in** (disabled by default). This allows easy local development without API keys. Always set `AUTH_ENABLED=true` for production deployments.

**[Security Overview](docs/SECURITY.md)** | **[RBAC Guide](docs/RBAC.md)**

---

## Event Sourcing

Every data change is stored as an immutable event for audit trails, time-travel debugging, and disaster recovery:

```python
# Get all events
events = await httpx.get(
    "http://localhost:8001/api/projects/my-app/events",
    params={"since": "0", "count": 100}
)

# Time-travel: reconstruct state at any point
# Compliance: complete audit trail
# Recovery: export and replay events
```

**[Event Sourcing Guide](docs/EVENT_SOURCING.md)** - Time-travel debugging, disaster recovery, analytics

---

## API Reference

**Core Endpoints (v1):**
- `POST /api/v1/data/publish` - Publish data
- `POST /api/v1/agents/register` - Register agent
- `POST /api/v1/query` - Query data (ad-hoc, no registration needed)
- `GET /api/v1/projects/{id}/events` - Get event stream
- `GET /api/v1/projects/{id}/export` - Export project data
- `POST /api/v1/projects/{id}/import` - Import project data

**Auth Endpoints:**
- `POST /api/v1/auth/keys` - Create API key
- `POST /api/v1/auth/roles` - Assign role
- `DELETE /api/v1/auth/keys/{id}` - Revoke key

**Monitoring:**
- `GET /health` - Basic health check
- `GET /api/v1/health` - Detailed health check with dependencies
- `GET /api/v1/metrics` - Prometheus metrics

**Interactive API Docs:** http://localhost:8001/docs

**Note:** Legacy `/api/*` endpoints (without `/v1`) are deprecated but still supported for backward compatibility.

---

## Hybrid Search

Contex supports hybrid search that combines:
- **BM25 Lexical Search** - Traditional keyword matching for exact terms
- **Semantic Vector Search** - AI-powered understanding of meaning
- **Reciprocal Rank Fusion (RRF)** - Intelligent merging of both approaches

Enable hybrid search by setting `HYBRID_SEARCH_ENABLED=true` in your environment. This requires OpenSearch to be running.

**Benefits:**
- Better accuracy for queries with specific technical terms
- Handles both exact matches and semantic similarity
- Improves results for domain-specific terminology

**Example:**
```python
# Query with hybrid search finds both semantic matches AND exact keyword matches
response = await client.query(
    project_id="my-app",
    queries=["authentication with JWT tokens"]
)
# Returns: Matches that contain "JWT" OR are semantically similar to authentication
```

---

## Data Management

**Automatic Retention Policies:**
- Events auto-expire after 30 days (configurable)
- Audit logs retained per compliance requirements
- Agent registrations expire after 7 days of inactivity

**Export/Import:**
```python
# Export project data (includes events, embeddings, and metadata)
export_data = await httpx.get(f"http://localhost:8001/api/v1/projects/my-app/export")

# Import to new environment
await httpx.post(
    f"http://localhost:8001/api/v1/projects/my-app-backup/import",
    json=export_data.json()
)
```

**Backup Strategy:**
- PostgreSQL WAL (Write-Ahead Logging) for durability
- Automated export via API for cross-region backup
- Point-in-time recovery using event sourcing

---

## Observability

**Structured Logging:**
- JSON-formatted logs for production
- Request IDs for correlation
- Contextual fields (project_id, agent_id)

**Prometheus Metrics:**
```bash
# Available at http://localhost:8001/api/v1/metrics
contex_agents_registered_total
contex_events_published_total
contex_queries_total
contex_query_duration_seconds
contex_db_connections
```

**Distributed Tracing:**
- OpenTelemetry instrumentation
- Trace IDs in responses and logs
- Compatible with Jaeger, Tempo, and other collectors

---

## Architecture

```
┌─────────────┐
│   Agents    │ ← Describe needs in natural language
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Contex    │ ← Semantic matching + hybrid search
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Publishers │ ← Publish context (JSON, YAML, etc.)
└─────────────┘
```

**[Architecture Guide](docs/ARCHITECTURE.md)** | **[Contributing](CONTRIBUTING.md)**

---

## Development

```bash
# Clone repository
git clone https://github.com/cahoots-org/contex.git
cd contex

# Start services
docker compose up -d

# Run tests
pytest tests/ -v

# Install SDK locally
cd sdk/python
pip install -e ".[dev]"
```

---

## Documentation

- **[Python SDK](sdk/python/README.md)** - Client library documentation
- **[Security](docs/SECURITY.md)** - Authentication, RBAC, rate limiting
- **[Database Setup](docs/DATABASE.md)** - PostgreSQL + pgvector configuration
- **[Event Sourcing](docs/EVENT_SOURCING.md)** - Time-travel queries and compliance
- **[RBAC](docs/RBAC.md)** - Role-based access control guide
- **[Rate Limiting](docs/RATE_LIMITING.md)** - Protection and limits
- **[Metrics](docs/METRICS.md)** - Prometheus metrics and monitoring
- **[Logging](docs/LOGGING.md)** - Structured logging and observability
- **[Golden Tests](tests/README_GOLDEN_TESTS.md)** - Integration tests and git bisect
- **[Operational Runbooks](docs/RUNBOOKS.md)** - Incident response and operations
- **[API Docs](http://localhost:8001/docs)** - Interactive API reference

**Configuration:**
- **[.env.example](.env.example)** - All environment variables documented

---

## Examples

See the [examples/](examples/) directory:
- `basic_usage.py` - Publish and query
- `agent_registration.py` - Agent setup
- `webhook_agent.py` - Webhook notifications
- `error_handling.py` - Error patterns
- `batch_operations.py` - Batch publishing

---

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- **[PyPI Package](https://pypi.org/project/contex-python/)**
- **[GitHub](https://github.com/cahoots-org/contex)**
- **[Issues](https://github.com/cahoots-org/contex/issues)**
