# Contex

**Semantic context routing for AI agent systems**

[![Tests](https://img.shields.io/badge/tests-154%20passing-success)](tests/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Contex delivers relevant project context to AI agents using semantic matching. Agents describe their needs in natural language, and Contex automatically routes matching data with real-time updates—no schemas, no polling.

## Features

- **Semantic Matching** - AI-powered filtering using sentence transformers
- **Real-time Updates** - Redis pub/sub or webhooks for instant notifications
- **Schema-Free** - Publish any JSON structure, no schema required
- **Hybrid Search** - Combines semantic (vector) and keyword (BM25) search
- **Event Sourcing** - Complete audit trail enables time-travel queries, debugging, and compliance
- **Multi-Project** - Isolated namespaces for different projects

---

## Quick Start

### 1. Install the Python SDK

```bash
pip install contex-python
```

### 2. Start Contex Server

**Option A: Docker Compose (Recommended)**

Create a `docker-compose.yml`:

```yaml
services:
  contex:
    image: ghcr.io/cahoots-org/contex:latest
    ports:
      - "8001:8001"
    environment:
      - REDIS_URL=redis://redis:6379
      - OPENSEARCH_URL=http://opensearch:9200
    depends_on:
      - redis
      - opensearch

  redis:
    image: redis/redis-stack:latest
    ports:
      - "6379:6379"

  opensearch:
    image: opensearchproject/opensearch:2.11.0
    environment:
      - discovery.type=single-node
      - DISABLE_SECURITY_PLUGIN=true
    ports:
      - "9200:9200"
```

Then run:
```bash
docker compose up -d
curl http://localhost:8001/api/health
```

**Option B: Clone and Run (for development)**

```bash
git clone https://github.com/cahoots-org/contex.git
cd contex
docker compose up -d
```

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

    # Agent receives matched data automatically
    for match in response.matched_data:
        print(f"Matched: {match.data_key} (score: {match.similarity_score:.2f})")
```

**SDK Features:**
- ✅ Async & sync clients
- ✅ Type hints with Pydantic models
- ✅ Automatic retries & rate limiting
- ✅ Comprehensive error handling

**Learn More:** [Python SDK Documentation](sdk/python/README.md) | [PyPI Package](https://pypi.org/project/contex-python/)

---

## How It Works

### 1. Publish Data (Any Format)

Contex accepts JSON, YAML, TOML, or plain text—publish however your data is stored.

<details open>
<summary><b>📦 JSON</b> - Structured data</summary>

```python
from contex import ContexAsyncClient

async with ContexAsyncClient(url="http://localhost:8001") as client:
    await client.publish(
        project_id="my-app",
        data_key="api_config",
        data={
            "base_url": "https://api.example.com",
            "timeout": 30,
            "retry_count": 3,
            "endpoints": {
                "users": "/v1/users",
                "orders": "/v1/orders"
            }
        }
    )
```
</details>

<details>
<summary><b>📄 YAML</b> - Configuration files</summary>

```python
from contex import ContexAsyncClient

yaml_content = """
database:
  host: localhost
  port: 5432
  name: myapp_production
  pool_size: 10
  timeout: 30

redis:
  host: redis.example.com
  port: 6379
  db: 0
  max_connections: 50
"""

async with ContexAsyncClient(url="http://localhost:8001") as client:
    await client.publish(
        project_id="my-app",
        data_key="infrastructure_config",
        data=yaml_content,
        data_format="yaml"
    )
```
</details>

<details>
<summary><b>⚙️ TOML</b> - Package configuration</summary>

```python
from contex import ContexAsyncClient

toml_content = """
[tool.poetry]
name = "my-app"
version = "1.2.3"
python = "^3.11"

[tool.poetry.dependencies]
fastapi = "^0.104.0"
redis = "^5.0.0"
pydantic = "^2.0.0"
"""

async with ContexAsyncClient(url="http://localhost:8001") as client:
    await client.publish(
        project_id="my-app",
        data_key="project_config",
        data=toml_content,
        data_format="toml"
    )
```
</details>

<details>
<summary><b>📝 Plain Text</b> - Policies and guidelines</summary>

```python
from contex import ContexAsyncClient

guidelines = """
Code Review Guidelines

1. All PRs require 2 approvals before merge
2. PRs over 500 lines need architecture team review
3. All tests must pass before merge
4. No direct commits to main branch
"""

async with ContexAsyncClient(url="http://localhost:8001") as client:
    await client.publish(
        project_id="my-app",
        data_key="dev_guidelines",
        data=guidelines,
        data_format="text"
    )
```
</details>

### 2. Register Agent with Semantic Needs

```python
from contex import ContexAsyncClient

async with ContexAsyncClient(url="http://localhost:8001") as client:
    response = await client.register_agent(
        agent_id="code-reviewer",
        project_id="my-app",
        data_needs=[
            "code style guidelines and linting rules",
            "testing requirements and coverage goals"
        ]
    )

    # Get matched data
    for match in response.matched_data:
        print(f"{match.data_key}: {match.similarity_score:.2f}")
```

**Returns:** Matched data + notification channel

### 3. Receive Updates

**Option A: Redis Pub/Sub**
```python
import redis.asyncio as redis

r = await redis.from_url("redis://localhost:6379")
pubsub = r.pubsub()
await pubsub.subscribe("agent:code-reviewer:updates")

async for message in pubsub.listen():
    if message["type"] == "message":
        update = json.loads(message["data"])
        # Process updated context
```

**Option B: Webhooks**
```python
# Register with webhook
await httpx.post("http://localhost:8001/api/agents/register", json={
    "agent_id": "code-reviewer",
    "project_id": "my-app",
    "data_needs": ["code style guidelines"],
    "notification_method": "webhook",
    "webhook_url": "https://my-agent.com/webhook",
    "webhook_secret": "shared-secret"
})
```

---

## Event Sourcing

Every data change is stored as an immutable event in Redis Streams. Query historical events to:

- **🕐 Time-Travel Debug** - See exactly what data an agent had at any point in time
- **📊 Compliance & Audit** - Complete trail of all data changes for regulatory requirements
- **🔧 Disaster Recovery** - Export and replay events to rebuild system state
- **📈 Analytics** - Analyze patterns in how your context evolves
- **🧪 Testing** - Clone production data into test environments

**Quick Example:**

```python
# Get all events since project start
events = await httpx.get(
    "http://localhost:8001/api/projects/my-app/events",
    params={"since": "0", "count": 100}
)
```

**Learn More:** [Event Sourcing Guide](docs/EVENT_SOURCING.md) - Complete guide with time-travel debugging, disaster recovery, and analytics examples.

---

## API Reference

### Core Endpoints

**Publish Data**
```http
POST /api/data/publish
Content-Type: application/json

{
  "project_id": "my-project",
  "data_key": "config",
  "data": { /* any JSON */ }
}
```

**Register Agent**
```http
POST /api/agents/register
Content-Type: application/json

{
  "agent_id": "my-agent",
  "project_id": "my-project",
  "data_needs": ["natural language descriptions"],
  "notification_method": "redis" | "webhook"
}
```

**Query/Search Data**
```http
POST /api/projects/{project_id}/query
Content-Type: application/json

{
  "query": "authentication methods OAuth JWT",
  "top_k": 5
}
```

**List Agents**
```http
GET /api/agents
GET /api/agents/{agent_id}
DELETE /api/agents/{agent_id}
```

### Additional Endpoints

- `GET /api/projects/{project_id}/data` - List all project data
- `GET /api/projects/{project_id}/events` - Get event history
- `GET /api/health` - Health check
- `GET /api/metrics` - Prometheus metrics
- `GET /api/docs` - Interactive API documentation

---

## Docker Deployment

### Configuration

**Environment Variables** (`.env` or `docker-compose.yml`):
```bash
# Redis
REDIS_URL=redis://redis:6379

# Semantic matching
SIMILARITY_THRESHOLD=0.5    # Match threshold (0-1)
MAX_MATCHES=10              # Max results per query
MAX_CONTEXT_SIZE=51200      # Max context tokens

# Logging
LOG_LEVEL=INFO
LOG_JSON=true

# Hybrid search
HYBRID_SEARCH_ENABLED=false
BM25_WEIGHT=0.7
KNN_WEIGHT=0.3
```

### Common Commands

```bash
# Start services
docker compose up -d

# View logs
docker compose logs -f contex

# Rebuild after code changes
docker compose build contex
docker compose up -d

# Stop services
docker compose down

# Remove all data (WARNING: destructive)
docker compose down -v

# Check resource usage
docker stats contex-app
```

### Health Checks

Services include health checks:

- **Redis**: `redis-cli ping` (every 5s)
- **Contex**: `GET /api/health` (every 10s)
- **OpenSearch**: Built-in health check (every 10s)

Check status:
```bash
docker compose ps
```

### Resource Limits

Default limits (configurable in `docker-compose.yml`):

**Contex**:
- Memory: 2GB limit, 1GB reserved
- CPU: 2.0 limit, 1.0 reserved

**Redis**:
- Memory: 512MB limit, 256MB reserved

**OpenSearch**:
- Memory: 2GB limit (via `OPENSEARCH_JAVA_OPTS`)

### Data Persistence

Data persists in Docker volumes:
- `redis-data` - Redis event store and indices
- `opensearch-data` - OpenSearch vector and keyword indices

**Backup Redis data:**
```bash
docker compose exec redis redis-cli BGSAVE
docker cp contex-redis:/data/dump.rdb ./backup-$(date +%Y%m%d).rdb
```

**Restore Redis data:**
```bash
docker compose down
docker cp ./backup.rdb contex-redis:/data/dump.rdb
docker compose up -d
```

See [`scripts/backup-redis.sh`](scripts/backup-redis.sh) for automated backups.

---

## Configuration

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `SIMILARITY_THRESHOLD` | `0.5` | Minimum similarity score (0-1) |
| `MAX_MATCHES` | `10` | Maximum results per query |
| `MAX_CONTEXT_SIZE` | `51200` | Maximum context size in tokens |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOG_JSON` | `true` | Output JSON-structured logs |

### Hybrid Search

| Variable | Default | Description |
|----------|---------|-------------|
| `HYBRID_SEARCH_ENABLED` | `false` | Enable hybrid search |
| `BM25_WEIGHT` | `0.7` | Weight for keyword matching (0-1) |
| `KNN_WEIGHT` | `0.3` | Weight for semantic matching (0-1) |

---

## Development

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Install dev dependencies (optional)
pip install -r requirements-dev.txt

# Start infrastructure
docker compose up -d redis opensearch
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run specific test file
pytest tests/test_context_engine.py -v
```

**Test Coverage:** 154 tests, 100% passing

### Project Structure

```
contex/
├── src/
│   ├── api/          # REST API routes
│   ├── core/         # Core engine and matching logic
│   ├── web/          # Web UI
│   └── models/       # Data models
├── tests/            # Test suite
├── docs/             # Documentation
├── k8s/              # Kubernetes manifests
├── helm/             # Helm chart
├── scripts/          # Utility scripts
├── examples/         # Usage examples
└── main.py           # Application entry point
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Contex Service                     │
│                                                     │
│  ┌─────────────────────────────────────────────┐  │
│  │   Sentence Transformers (all-MiniLM-L6-v2)  │  │
│  └────────────────┬────────────────────────────┘  │
│                   │                                │
│  ┌────────────────▼────────────────────────────┐  │
│  │        Semantic Matcher + Hybrid Search     │  │
│  │         (Vector + BM25 via OpenSearch)      │  │
│  └────────────────┬────────────────────────────┘  │
│                   │                                │
│  ┌────────────────▼────────────────────────────┐  │
│  │     Context Engine (Filtering + Routing)    │  │
│  └────────────────┬────────────────────────────┘  │
│                   │                                │
│  ┌────────────────▼────────────────────────────┐  │
│  │    Event Store (Redis Streams) + Pub/Sub    │  │
│  └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
         │                              ▲
         │ (push updates)               │ (publish/register)
         ▼                              │
   AI Agents ──────────────► Data Publishers
```

**Technology Stack:**
- **API**: FastAPI (Python 3.11+)
- **Embeddings**: sentence-transformers (all-MiniLM-L6-v2, 384 dims)
- **Event Store**: Redis Streams
- **Vector Search**: OpenSearch with kNN
- **Keyword Search**: OpenSearch with BM25
- **Notifications**: Redis pub/sub or HTTP webhooks

**Resource Requirements:**
- Memory: ~2GB (400MB model + 500MB runtime + buffer)
- CPU: 2+ cores recommended (CPU-only, no GPU required)
- Storage: Depends on data volume (Redis + OpenSearch)

---

## Kubernetes Deployment

Production-ready Kubernetes manifests and Helm chart available:

```bash
# Using kubectl with Kustomize
kubectl apply -k k8s/overlays/prod

# Using Helm (recommended)
helm install contex ./helm/contex \
  --set redis.enabled=true \
  --set resources.memory=2Gi \
  -f production-values.yaml
```

Features:
- Liveness and readiness probes
- Horizontal Pod Autoscaler (2-10 replicas)
- Resource limits and requests
- ServiceMonitor for Prometheus
- Ingress with TLS support

See [`docs/KUBERNETES.md`](docs/KUBERNETES.md) for complete guide.

---

## Use Cases

- **AI Coding Assistants** - Code generators, test writers, doc agents that adapt to your codebase
- **Content Pipelines** - Multi-stage workflows where agents pass context seamlessly
- **Code Review** - Quality checkers that enforce standards consistently
- **Documentation** - Agents that maintain docs based on code changes
- **Multi-Agent Systems** - Coordinating multiple specialized agents

Any scenario where you need to share data across agents without enforcing rigid schemas.

---

## Roadmap

### ✅ Completed (v0.2.0)
- [x] Semantic matching with embeddings
- [x] Event-driven updates via pub/sub
- [x] Historical catch-up for new agents
- [x] Multi-project support
- [x] Webhook support (alternative to Redis)
- [x] Ad-hoc query endpoint
- [x] Hybrid search (BM25 + kNN)
- [x] Structured JSON logging
- [x] Prometheus metrics
- [x] Health checks (liveness/readiness)
- [x] Kubernetes manifests + Helm chart

### 🚧 In Development
- [ ] API key authentication (code complete, needs deployment)
- [ ] Rate limiting (code complete, needs deployment)
- [ ] RBAC (code complete, needs deployment)
- [ ] Circuit breaker for webhooks (code complete, needs testing)

### 📋 Planned (v0.3.0+)
- [ ] Python SDK
- [ ] Node.js/TypeScript SDK
- [ ] Distributed tracing (OpenTelemetry)
- [ ] Data retention policies
- [ ] Export/import functionality
- [ ] GraphQL API
- [ ] WebSocket support

See [`ROADMAP.md`](ROADMAP.md) for detailed planning.

---

## FAQ

**Q: How is this different from a vector database?**
A: Vector DBs are for retrieval (query → results). Contex is for subscription (needs → live updates). Agents don't query—they subscribe and get pushed updates when relevant data changes.

**Q: Different from RAG?**
A: RAG is pull-based (agent requests context). Contex is push-based (context sent when it changes). Better for long-running agents that need to stay updated.

**Q: Do I need a GPU?**
A: No. Runs on CPU with sentence-transformers (~10ms per embedding on modern CPUs).

**Q: Can I use without Redis?**
A: Agents can use webhooks instead of Redis pub/sub for notifications. However, Contex itself requires Redis for the event store and indices.

**Q: What about authentication?**
A: Authentication, rate limiting, and RBAC are implemented but not yet deployed by default. See security roadmap above.

**Q: How does hybrid search work?**
A: Combines semantic (vector/kNN) and keyword (BM25) search. Results are scored using weighted combination (default: 70% BM25, 30% kNN). Enable with `HYBRID_SEARCH_ENABLED=true`.

---

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Code of conduct
- Development setup
- Testing guidelines
- Pull request process

---

## License

MIT License - See [LICENSE](LICENSE) for details.

---

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/cahoots-org/contex/issues)
- **Discussions**: [GitHub Discussions](https://github.com/cahoots-org/contex/discussions)

---

<p align="center">
  Built with ❤️ for AI agent developers
</p>
