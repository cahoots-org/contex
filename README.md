# Contex

**Semantic context routing for AI agents**

Contex is a context engine that automatically delivers relevant project data to AI agents based on their semantic needs. Agents describe what they need in natural language, and Contex uses embedding-based matching to route the right information—updating them in real-time as your project evolves.

```python
# Agent declares needs
await contex.register_agent(
    agent_id="code-generator",
    data_needs=["coding standards", "API patterns", "database schemas"]
)

# Publish any data
await contex.publish(data_key="api_docs", data={...})

# Matching agents get notified automatically
```

**No schemas. No polling. Just semantic matching + real-time updates.**

---

## Why Contex?

**Without Contex:**
- Agents duplicate context-building logic
- Hardcoded queries to specific data sources
- Manual polling for updates
- Tight coupling between agents and data

**With Contex:**
- Agents declare what they need in natural language
- Semantic matching finds relevant data automatically
- Real-time updates via Redis pub/sub or webhooks
- Zero coupling—add/remove agents without code changes

---

## Quick Start

### 1. Start Contex

```bash
# Using Docker Compose (recommended)
docker compose up -d

# Or run locally:
pip install -r requirements.txt
python main.py

# Contex runs at http://localhost:8001
```

### 2. Publish Data (Any Structure)

```python
import httpx

# Publish whatever JSON makes sense for your project
await httpx.post("http://localhost:8001/data/publish", json={
    "project_id": "my-app",
    "data_key": "coding_standards",
    "data": {
        "style": "PEP 8",
        "max_line_length": 100,
        "quotes": "double"
    }
})
```

### 3. Register Agent

```python
# Agent describes needs in natural language
await httpx.post("http://localhost:8001/agents/register", json={
    "agent_id": "code-reviewer",
    "project_id": "my-app",
    "data_needs": [
        "code style guidelines and linting rules",
        "testing requirements and coverage goals"
    ]
})
# Returns: matched data + notification channel
```

### 4. Receive Updates

**Option A: Redis Pub/Sub (default)**
```python
import redis.asyncio as redis

# Subscribe to agent's update channel
r = await redis.from_url("redis://localhost:6379")
pubsub = r.pubsub()
await pubsub.subscribe("agent:code-reviewer:updates")

async for message in pubsub.listen():
    if message["type"] == "message":
        update = json.loads(message["data"])
        # Use updated context in your agent
```

**Option B: Webhooks (no Redis required)**
```python
# Register with webhook
await httpx.post("http://localhost:8001/agents/register", json={
    "agent_id": "code-reviewer",
    "project_id": "my-app",
    "data_needs": ["code style guidelines"],
    "notification_method": "webhook",
    "webhook_url": "https://my-agent.com/webhook",
    "webhook_secret": "shared-secret"
})

# Contex will POST updates to your webhook endpoint
# See examples/webhook_agent.py for full implementation
```

---

## How It Works

### Schema-Free Publishing

Publish whatever JSON you want. Since Contex uses embeddings to match data, it doesn't matter what structure your data has.

```python
# API documentation? Absolutely.
{"endpoints": [{...}], "auth": "OAuth2", "rate_limit": "100/min"}

# Database schema? Go for it.
{"tables": {"users": {"id": "uuid", "email": "string"}}, "indexes": [...]}

# Team info? Why not.
{"on_call": "alice@example.com", "timezone": "PST", "slack_channel": "#backend"}

# Even your custom DSL? Yep.
{"rules": [{"if": "PR > 100 lines", "then": "require 2 reviewers"}]}
```

**The point:** Your data structure is your choice. Contex just helps agents find what they need.

### Semantic Matching

When an agent needs **"API authentication and rate limiting"**, Contex:

1. Generates embeddings for all project data
2. Finds semantic similarity with agent needs (cosine similarity)
3. Returns matches above threshold (default: 0.5)

The agent gets `api_routes` and `rate_limit_config`, but not `coding_standards`.

### Real-Time Updates

```
Data published → Contex stores in event log
              → Finds agents with matching needs
              → Pushes updates via Redis pub/sub OR webhooks
              → Agents receive fresh context
```

No polling. No staleness.

---

## Use Cases

**🤖 AI Coding Assistants** - Code generators, test writers, doc agents that adapt to your codebase

**📝 Content Pipelines** - Multi-stage workflows where agents pass context seamlessly

**🔍 Code Review** - Quality checkers that enforce your standards consistently

Any use case where you need to share data across agents, but dont want to (or can't) enforce a schema.

---

## API Reference

### Register Agent (Redis)
```http
POST /agents/register
{
  "agent_id": "my-agent",
  "project_id": "my-project",
  "data_needs": ["natural language descriptions"],
  "notification_method": "redis"
}
```

### Register Agent (Webhook)
```http
POST /agents/register
{
  "agent_id": "my-agent",
  "project_id": "my-project",
  "data_needs": ["natural language descriptions"],
  "notification_method": "webhook",
  "webhook_url": "https://my-agent.com/webhook",
  "webhook_secret": "optional-hmac-secret"
}
```

### Publish Data
```http
POST /data/publish
{
  "project_id": "my-project",
  "data_key": "anything",
  "data": { /* any JSON */ }
}
```

### List Agents
```http
GET /agents
GET /agents/{agent_id}
DELETE /agents/{agent_id}
```

### Ad-hoc Query (NEW)
```http
POST /projects/{project_id}/query
{
  "query": "What authentication methods are we using?",
  "top_k": 5
}
```

### Query Data
```http
GET /projects/{project_id}/events?since=0&count=100
GET /projects/{project_id}/data
```

---

## Configuration

```bash
# .env
REDIS_URL=redis://localhost:6379
SIMILARITY_THRESHOLD=0.5  # Match threshold (0-1)
MAX_MATCHES=10            # Max results per need
MAX_CONTEXT_SIZE=51200    # Max context tokens (~40% of 128k window)
```

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7-alpine

  contex:
    image: contex/contex:latest
    environment:
      - REDIS_URL=redis://redis:6379
    ports:
      - "8001:8001"
    deploy:
      resources:
        limits:
          memory: 2GB
          cpus: '2.0'
```

---

## Architecture

```
┌─────────────────────────────────────────┐
│           Contex Service                │
│                                         │
│  Sentence Transformers (embeddings)    │
│  ↓                                      │
│  Semantic Matcher                      │
│  ↓                                      │
│  Redis Event Store + Pub/Sub          │
└─────────────────────────────────────────┘
         ↓ (updates)        ↑ (register/publish)
    AI Agents           Data Publishers
```

**Core:**
- **Semantic Matcher** - sentence-transformers for embeddings
- **Event Store** - Redis Streams (append-only log)
- **Notifications** - Redis pub/sub or HTTP webhooks
- **REST API** - FastAPI (Python 3.11)

**Resources:**
- Memory: ~2GB (400MB model + 500MB runtime + headroom)
- CPU: 2+ cores recommended
- Stateless (scales horizontally)

---

## Deployment

### Docker
```bash
docker run -p 8001:8001 \
  -e REDIS_URL=redis://redis:6379 \
  contex/contex:latest
```

### Kubernetes
```bash
helm install contex contex/contex \
  --set redis.enabled=true \
  --set resources.memory=2Gi
```

### Cloud Run / ECS / Azure
```bash
# Fully managed container platforms
gcloud run deploy contex --image contex/contex:latest
```

---

## Client SDKs

### Python (Coming Soon)
```python
from contex import ContexClient

client = ContexClient(url="http://localhost:8001")
await client.register(agent_id="...", needs=[...])
async for update in client.listen():
    print(update)
```

### Node.js (Coming Soon)
```javascript
import { ContexClient } from '@contex/client';

const client = new ContexClient({ url: 'http://localhost:8001' });
client.on('update', (data) => console.log(data));
```

---

## Development

```bash
# Install
pip install -r requirements.txt

# Run locally
python main.py

# Test
pytest tests/ -v

# With coverage
pytest tests/ --cov=src --cov-report=html
```

---

## FAQ

**Q: How is this different from a vector database?**
A: Vector DBs are for retrieval (query → results). Contex is for subscription (needs → live updates). Agents don't query—they subscribe and get pushed updates.

**Q: Different from RAG?**
A: RAG is pull-based (agent requests context). Contex is push-based (context sent when it changes).

**Q: Do I need a GPU?**
A: No. CPU-only with sentence-transformers (~10ms per embedding).

**Q: Can I use without Redis?**
A: Agents can use webhooks instead of Redis pub/sub for notifications. However, Contex itself still requires Redis for the event store.

**Q: What about authentication?**
A: Designed for internal services (no auth included). Add a reverse proxy (nginx, Envoy) if exposing externally.

---

## Roadmap

- [x] Semantic matching with embeddings
- [x] Event-driven updates via pub/sub
- [x] Historical catch-up for new agents
- [x] Multi-project support
- [x] Webhook support (alternative to Redis)
- [x] Ad-hoc query endpoint
- [ ] Python SDK
- [ ] Node.js SDK
- [ ] Authentication layer
- [ ] Prometheus metrics
- [ ] Helm chart

See [ROADMAP.md](ROADMAP.md) for detailed plans.

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT License - See [LICENSE](LICENSE).

---

[Documentation](https://docs.contex.dev) • [GitHub](https://github.com/contex/contex) • [Discord](https://discord.gg/contex)
