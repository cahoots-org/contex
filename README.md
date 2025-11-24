# Contex

**Semantic context routing for AI agent systems**

[![Tests](https://img.shields.io/badge/tests-154%20passing-success)](tests/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Contex delivers relevant project context to AI agents using semantic matching. Agents describe their needs in natural language, and Contex automatically routes matching data with real-time updates—no schemas, no polling.

## Features

- **Semantic Matching** - AI-powered filtering using sentence transformers
- **Real-time Updates** - Redis pub/sub or webhooks for instant notifications
- **Schema-Free** - Publish JSON, YAML, TOML, or plain text
- **Event Sourcing** - Complete audit trail for time-travel queries and compliance
- **Security** - API key auth, RBAC, and rate limiting
- **Multi-Project** - Isolated namespaces with project-level permissions
- **Sandbox UI** - Interactive web interface for testing

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

    # Agent receives matched data automatically
    for match in response.matched_data:
        print(f"Matched: {match.data_key} (score: {match.similarity_score:.2f})")
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

# Contex returns semantically matched data
for match in response.matched_data:
    print(f"{match.data_key}: {match.similarity_score:.2f}")
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

**Core Endpoints:**
- `POST /api/data/publish` - Publish data
- `POST /api/agents/register` - Register agent
- `POST /api/data/query` - Query data
- `GET /api/projects/{id}/events` - Get event stream

**Auth Endpoints:**
- `POST /api/auth/keys` - Create API key
- `POST /api/auth/roles` - Assign role
- `DELETE /api/auth/keys/{id}` - Revoke key

**Interactive API Docs:** http://localhost:8001/docs

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
- **[Event Sourcing](docs/EVENT_SOURCING.md)** - Time-travel queries and compliance
- **[RBAC](docs/RBAC.md)** - Role-based access control guide
- **[Rate Limiting](docs/RATE_LIMITING.md)** - Protection and limits
- **[Metrics](docs/METRICS.md)** - Prometheus metrics
- **[API Docs](http://localhost:8001/docs)** - Interactive API reference

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
