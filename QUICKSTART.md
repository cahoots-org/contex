# Contex Quick Start Guide

Get started with Contex in 5 minutes! This guide will walk you through setting up Contex and creating your first semantic context routing system.

## Prerequisites

- Docker and Docker Compose (recommended)
- OR Python 3.11+ and Redis 7.0+

## Option 1: Quick Start with Docker (Recommended)

### 1. Clone and Start

```bash
# Clone the repository
git clone https://github.com/contex/contex.git
cd contex

# Start Contex and Redis
docker compose up -d

# Check the logs
docker compose logs -f contex
```

That's it! Contex is now running at `http://localhost:8001`.

### 2. Verify It's Running

```bash
curl http://localhost:8001/health
# Output: {"status":"healthy"}
```

## Option 2: Local Setup (Without Docker)

### 1. Install Redis

**macOS:**
```bash
brew install redis
brew services start redis
```

**Ubuntu/Debian:**
```bash
sudo apt-get install redis-server
sudo systemctl start redis
```

**Windows:**
Download from [Redis.io](https://redis.io/download) or use WSL.

### 2. Install Contex

```bash
# Clone the repository
git clone https://github.com/contex/contex.git
cd contex

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start Contex
python main.py
```

Contex will start on `http://localhost:8001`.

## Your First Context Routing

Let's create a simple example where an agent needs coding standards and API documentation.

### 1. Publish Project Data

Save this as `publish_data.py`:

```python
import asyncio
import httpx

async def publish_project_data():
    """Publish some project data to Contex"""

    contex_url = "http://localhost:8001"
    project_id = "my-app"

    # Publish coding standards
    await httpx.AsyncClient().post(
        f"{contex_url}/data/publish",
        json={
            "project_id": project_id,
            "data_key": "coding_standards",
            "data": {
                "style": "PEP 8",
                "max_line_length": 100,
                "quotes": "double",
                "imports": "sorted alphabetically",
                "docstrings": "Google style"
            }
        }
    )
    print("✓ Published coding standards")

    # Publish API documentation
    await httpx.AsyncClient().post(
        f"{contex_url}/data/publish",
        json={
            "project_id": project_id,
            "data_key": "api_endpoints",
            "data": {
                "base_url": "https://api.example.com/v1",
                "authentication": "Bearer token",
                "endpoints": [
                    {"path": "/users", "methods": ["GET", "POST"]},
                    {"path": "/posts", "methods": ["GET", "POST", "PUT", "DELETE"]}
                ]
            }
        }
    )
    print("✓ Published API endpoints")

    # Publish database schema
    await httpx.AsyncClient().post(
        f"{contex_url}/data/publish",
        json={
            "project_id": project_id,
            "data_key": "database_schema",
            "data": {
                "database": "PostgreSQL 15",
                "tables": {
                    "users": {
                        "id": "uuid PRIMARY KEY",
                        "email": "varchar(255) UNIQUE",
                        "created_at": "timestamp"
                    },
                    "posts": {
                        "id": "uuid PRIMARY KEY",
                        "user_id": "uuid REFERENCES users(id)",
                        "title": "varchar(200)",
                        "content": "text",
                        "created_at": "timestamp"
                    }
                }
            }
        }
    )
    print("✓ Published database schema")

if __name__ == "__main__":
    asyncio.run(publish_project_data())
```

Run it:
```bash
python publish_data.py
```

### 2. Register an Agent (Redis Pub/Sub)

Save this as `agent_redis.py`:

```python
import asyncio
import json
import httpx
import redis.asyncio as redis

async def run_agent():
    """Register an agent and listen for updates via Redis"""

    contex_url = "http://localhost:8001"
    project_id = "my-app"
    agent_id = "code-generator"

    # Register agent with its data needs
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{contex_url}/agents/register",
            json={
                "agent_id": agent_id,
                "project_id": project_id,
                "data_needs": [
                    "code style guidelines and formatting rules",
                    "API endpoints and authentication methods",
                    "database tables and relationships"
                ],
                "notification_method": "redis"
            }
        )

        result = response.json()
        print(f"✓ Agent registered: {agent_id}")
        print(f"  Matched needs: {result['matched_needs']}")
        print(f"  Notification channel: {result['notification_channel']}")
        channel = result['notification_channel']

    # Subscribe to updates via Redis
    r = await redis.from_url("redis://localhost:6379")
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)

    print(f"\n👂 Listening for updates on {channel}...")
    print("(Press Ctrl+C to stop)\n")

    async for message in pubsub.listen():
        if message["type"] == "message":
            update = json.loads(message["data"])

            if update["type"] == "initial_context":
                print("📥 Received initial context:")
                for need, matches in update["context"].items():
                    print(f"\n  Need: '{need}'")
                    for match in matches:
                        print(f"    - {match['data_key']} (similarity: {match['similarity']:.2f})")

            elif update["type"] == "data_update":
                print(f"\n🔄 Data updated: {update['data_key']}")
                print(f"   Sequence: {update['sequence']}")
                print(f"   Data: {json.dumps(update['data'], indent=2)}")

if __name__ == "__main__":
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("\n\n👋 Agent stopped")
```

Run it:
```bash
python agent_redis.py
```

You should see:
```
✓ Agent registered: code-generator
  Matched needs: {'code style guidelines...': 1, 'API endpoints...': 1, ...}
  Notification channel: agent:code-generator:updates

👂 Listening for updates...

📥 Received initial context:
  Need: 'code style guidelines and formatting rules'
    - coding_standards (similarity: 0.78)
  Need: 'API endpoints and authentication methods'
    - api_endpoints (similarity: 0.82)
  Need: 'database tables and relationships'
    - database_schema (similarity: 0.75)
```

### 3. Register an Agent (Webhook)

For agents that prefer webhooks instead of Redis:

Save this as `agent_webhook.py`:

```python
import asyncio
import json
import httpx
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()

@app.post("/webhook")
async def handle_webhook(request: Request):
    """Receive updates from Contex"""
    body = await request.body()
    payload = json.loads(body)

    if payload["type"] == "initial_context":
        print("📥 Received initial context:")
        for need, matches in payload["context"].items():
            print(f"\n  Need: '{need}'")
            for match in matches:
                print(f"    - {match['data_key']} (similarity: {match['similarity']:.2f})")

    elif payload["type"] == "data_update":
        print(f"\n🔄 Data updated: {payload['data_key']}")
        print(f"   Data: {json.dumps(payload['data'], indent=2)}")

    return {"status": "received"}

async def register():
    """Register agent with webhook"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8001/agents/register",
            json={
                "agent_id": "webhook-agent",
                "project_id": "my-app",
                "data_needs": [
                    "code style guidelines",
                    "API documentation"
                ],
                "notification_method": "webhook",
                "webhook_url": "http://host.docker.internal:8002/webhook",
                "webhook_secret": "my-secret-key"
            }
        )
        print(f"✓ Agent registered via webhook")
        print(f"  Status: {response.json()['status']}")

if __name__ == "__main__":
    # Register first
    asyncio.run(register())

    # Start webhook server
    print("\n👂 Webhook server listening on http://localhost:8002")
    uvicorn.run(app, host="0.0.0.0", port=8002)
```

Run it:
```bash
python agent_webhook.py
```

### 4. Query Project Data (No Agent Registration)

For one-off queries without registering an agent:

```python
import asyncio
import httpx
import json

async def query_data():
    """Query project data without registering an agent"""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8001/projects/my-app/query",
            json={
                "query": "What coding standards are we using?",
                "top_k": 3
            }
        )

        result = response.json()
        print(f"Query: {result['query']}")
        print(f"Found {result['total_matches']} matches:\n")

        for match in result['matches']:
            print(f"  📄 {match['data_key']} (similarity: {match['similarity']:.2f})")
            print(f"     {json.dumps(match['data'], indent=4)}\n")

if __name__ == "__main__":
    asyncio.run(query_data())
```

Run it:
```bash
python query_example.py
```

## Testing It Out

### 1. Publish New Data

While your agent is running, publish new data:

```python
import asyncio
import httpx

async def update_standards():
    await httpx.AsyncClient().post(
        "http://localhost:8001/data/publish",
        json={
            "project_id": "my-app",
            "data_key": "coding_standards",
            "data": {
                "style": "PEP 8",
                "max_line_length": 120,  # Changed!
                "quotes": "double",
                "type_hints": "required"  # New!
            }
        }
    )
    print("✓ Updated coding standards")

asyncio.run(update_standards())
```

Your agent will receive the update automatically! 🎉

### 2. Check Registered Agents

```bash
curl http://localhost:8001/agents
```

### 3. View Project Events

```bash
curl http://localhost:8001/projects/my-app/events
```

### 4. List Project Data

```bash
curl http://localhost:8001/projects/my-app/data
```

## Understanding Semantic Matching

Contex uses sentence-transformers to match agent needs with data:

- **Agent need**: "code style guidelines and formatting rules"
- **Data key**: "coding_standards"
- **Similarity**: 0.78 (high match!)

The matching happens automatically based on semantic similarity, not exact string matching. This means:

✅ "API documentation" matches "api_endpoints"
✅ "database schema" matches "database_schema"
✅ "authentication methods" matches data containing auth info

❌ "payment processing" won't match unrelated data

## Configuration

Adjust matching behavior via environment variables:

```yaml
# docker-compose.yml
environment:
  - SIMILARITY_THRESHOLD=0.5  # Lower = more matches (0-1)
  - MAX_MATCHES=10            # Max results per need
```

Or locally:
```bash
export SIMILARITY_THRESHOLD=0.6
export MAX_MATCHES=5
python main.py
```

## Next Steps

- **Read the [Full README](README.md)** for complete documentation
- **Check [Examples](examples/)** for more use cases
- **Read [Docker Setup](README_DOCKER.md)** for production deployment
- **Join the [Discord](https://discord.gg/contex)** for help and discussion

## Troubleshooting

### Port Already in Use

```bash
# Change port in docker-compose.yml
ports:
  - "8002:8001"  # Use 8002 instead of 8001
```

### Redis Connection Failed

```bash
# Check Redis is running
redis-cli ping
# Should return: PONG

# Or with Docker
docker compose ps
# Should show redis as healthy
```

### Agent Not Receiving Updates

1. Check agent is registered: `curl http://localhost:8001/agents`
2. Check similarity threshold (may be too high)
3. Check Redis connection in agent code
4. Check logs: `docker compose logs -f contex`

### Model Download Slow

The first startup downloads a ~80MB model. This is normal and only happens once.

## Clean Up

```bash
# Stop services (keeps data)
docker compose stop

# Stop and remove (keeps data)
docker compose down

# Remove everything including data
docker compose down -v
```

## Getting Help

- **GitHub Issues**: Report bugs or request features
- **Discussions**: Ask questions
- **Discord**: Real-time help
- **Documentation**: https://docs.contex.dev

---

**Happy routing!** 🚀
