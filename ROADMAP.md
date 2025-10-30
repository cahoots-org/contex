# Contex Roadmap

This document outlines the planned development path for Contex. Priorities may shift based on community feedback and real-world usage.

---

## Current Status (v0.2.0)

✅ **Core Features**
- Semantic matching with sentence-transformers
- Event-driven updates via Redis pub/sub
- Historical catch-up for new agents
- Multi-project support
- REST API (FastAPI)
- Docker deployment

✅ **What Works Today**
- Register agents with natural language needs
- Publish arbitrary JSON data
- Automatic semantic matching
- Real-time updates to agents
- Event log with replay capability

---

## Phase 1: Developer Experience (Q1 2025)

**Goal:** Make Contex trivial to integrate

### Client SDKs
- [ ] **Python SDK** - Pythonic API with async support
  ```python
  from contex import Contex

  async with Contex("http://localhost:8001") as ctx:
      await ctx.register("my-agent", needs=["..."])
      async for update in ctx.listen():
          print(update)
  ```

- [ ] **Node.js SDK** - TypeScript-first with event emitters
  ```javascript
  import { Contex } from '@contex/client';

  const contex = new Contex({ url: 'http://localhost:8001' });
  contex.on('update', (data) => console.log(data));
  ```

- [ ] **Go SDK** - Idiomatic Go with channels
  ```go
  client := contex.NewClient("http://localhost:8001")
  updates := client.Listen(ctx)
  for update := range updates { ... }
  ```

### Documentation
- [ ] Interactive examples on docs site
- [ ] Video tutorials (5-10 min each)
- [ ] Integration guides for popular frameworks:
  - LangChain
  - LlamaIndex
  - AutoGPT
  - Semantic Kernel
- [ ] Architecture deep-dive blog post

### Developer Tools
- [ ] CLI tool for testing/debugging
  ```bash
  contex publish --project my-app --key config --data '{...}'
  contex agents list --project my-app
  contex tail --agent my-agent  # Live updates
  ```
- [ ] Web UI for monitoring agents and data flow
- [ ] Local development mode (embedded Redis)

---

## Phase 2: Production Ready (Q2 2025)

**Goal:** Make Contex safe for production deployments

### Observability
- [ ] **Prometheus metrics**
  - Agent registration rate
  - Matching latency (p50, p95, p99)
  - Cache hit rate
  - Redis connection pool stats

- [ ] **OpenTelemetry tracing**
  - End-to-end trace from publish → match → notify
  - Integration with Jaeger/Zipkin

- [ ] **Structured logging**
  - JSON logs with correlation IDs
  - Log levels (DEBUG, INFO, WARN, ERROR)
  - Queryable via Loki/CloudWatch

### Reliability
- [ ] **Health checks**
  - `/health` with detailed status
  - Redis connectivity
  - Model loading status

- [ ] **Graceful shutdown**
  - Drain in-flight requests
  - Notify agents before shutdown

- [ ] **Rate limiting**
  - Per-project limits
  - Per-agent limits
  - Configurable via env vars

### Security
- [ ] **Authentication**
  - API key support
  - JWT validation
  - Per-project isolation

- [ ] **Authorization**
  - Agent permissions (read-only vs read-write)
  - Project-level access control

- [ ] **Input validation**
  - Max data size limits
  - JSON schema validation (optional)
  - Sanitization of data keys

### Deployment
- [ ] **Helm chart** for Kubernetes
  - StatefulSet for Redis
  - Deployment for Contex
  - ConfigMaps for configuration
  - Secrets for credentials

- [ ] **Terraform modules**
  - AWS (ECS + ElastiCache)
  - GCP (Cloud Run + Memorystore)
  - Azure (Container Apps + Redis)

---

## Phase 3: Scale & Flexibility (Q3 2025)

**Goal:** Handle large deployments and diverse use cases

### Performance
- [ ] **Caching improvements**
  - LRU cache for embeddings
  - TTL-based invalidation
  - Memory-mapped embeddings for large projects

- [ ] **Batch operations**
  - Bulk agent registration
  - Bulk data publishing
  - Batch matching (reduce latency)

- [ ] **Connection pooling**
  - Redis connection reuse
  - HTTP/2 for API calls

### Alternative Backends
- [ ] **Webhook support** (alternative to Redis pub/sub)
  ```json
  {
    "agent_id": "my-agent",
    "webhook_url": "https://my-app.com/contex/updates"
  }
  ```

- [ ] **Kafka support** (for high-throughput scenarios)
- [ ] **NATS support** (for edge deployments)
- [ ] **PostgreSQL** (alternative to Redis Streams for event log)

### Advanced Matching
- [ ] **Multi-language embeddings**
  - Support non-English data/needs
  - Language detection

- [ ] **Custom embedding models**
  - BYO model (bring your own)
  - Fine-tuned models for domain-specific data

- [ ] **Hybrid search**
  - Combine semantic + keyword matching
  - Boost exact matches

- [ ] **Negative matching**
  - Agents can specify what they DON'T need
  - Example: "API docs but NOT deprecated endpoints"

---

## Phase 4: Enterprise Features (Q4 2025)

**Goal:** Support large organizations with complex needs

### Multi-Tenancy
- [ ] **Namespace isolation**
  - Separate projects per customer
  - Data isolation guarantees

- [ ] **Usage tracking**
  - Per-tenant metrics
  - Billing-ready events

- [ ] **Quotas & limits**
  - Agent count per tenant
  - Data size per tenant
  - API rate limits per tenant

### Compliance
- [ ] **Audit logs**
  - Who published what, when
  - Agent registration/deletion events
  - Configuration changes

- [ ] **Data retention policies**
  - Configurable event log retention
  - Automatic purging of old data

- [ ] **GDPR compliance**
  - Right to erasure (delete project data)
  - Data export (download all project data)

### High Availability
- [ ] **Multi-region support**
  - Active-active deployment
  - Cross-region replication

- [ ] **Disaster recovery**
  - Backup/restore procedures
  - Point-in-time recovery

- [ ] **Zero-downtime upgrades**
  - Rolling deployments
  - Blue-green deployments

---

## Future Ideas (Backlog)

These are ideas we're considering but haven't committed to yet:

### Graph-Based Context
- Connect data entities with relationships
- Query graph to find related context
- Example: "agent needs X, X relates to Y, agent also gets Y"

### Smart Summarization
- LLM-powered summaries of matched data
- Reduce token usage for agents with large contexts
- Configurable summarization prompts

### Context Versioning
- Track how context evolves over time
- Time-travel queries ("what context did agent have at timestamp X?")
- Diff between versions

### Federated Contex
- Multiple Contex instances that talk to each other
- Share context across organizations
- Privacy-preserving matching

### Visual Editor
- Drag-and-drop agent configuration
- Visual data flow diagrams
- Real-time matching preview

### Marketplace
- Pre-built agent profiles for common use cases
- Community-contributed data schemas
- Templates for different industries

---

## How to Influence the Roadmap

We prioritize based on:
1. **User feedback** - What are people actually asking for?
2. **Adoption blockers** - What prevents production use?
3. **Community contributions** - What do contributors want to build?

**Ways to provide input:**
- 💬 [GitHub Discussions](https://github.com/contex/contex/discussions) - Propose features
- 🐛 [GitHub Issues](https://github.com/contex/contex/issues) - Report bugs or limitations
- 🎯 [Roadmap Voting](https://github.com/contex/contex/discussions/roadmap) - Vote on priorities
- 💡 [Discord](https://discord.gg/contex) - Chat with maintainers

---

## Contributing

Want to help build a feature from the roadmap?

1. Check if there's a GitHub issue for it
2. Comment on the issue with your approach
3. Wait for maintainer feedback
4. Submit a PR

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## Version History

**v0.2.0** (Current) - Semantic matching + real-time updates
**v0.1.0** - Initial prototype with Redis pub/sub

---

*Last updated: January 2025*
