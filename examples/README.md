# Contex Examples

This directory contains practical examples demonstrating how to use Contex.

## Prerequisites

1. **Start Contex**:
   ```bash
   cd ..
   docker compose up -d
   ```

2. **Install dependencies** (if running examples locally):
   ```bash
   pip install httpx redis
   ```

## Examples

### 1. Publishing Data

**File**: [`publish_data.py`](publish_data.py)

Demonstrates how to publish various types of project data to Contex:
- Coding standards
- API endpoints
- Database schema
- Testing requirements
- Deployment configuration

```bash
python examples/publish_data.py
```

**When to use**: When you want to make project information available to agents.

---

### 2. Agent with Redis Pub/Sub

**File**: [`agent_redis.py`](agent_redis.py)

Shows how to create an agent that:
- Registers with specific data needs
- Receives initial matched context
- Listens for real-time updates via Redis pub/sub

```bash
# Terminal 1: Start the agent
python examples/agent_redis.py

# Terminal 2: Publish data to see updates
python examples/publish_data.py
```

**When to use**: For agents that need real-time context updates and can maintain a Redis connection.

---

### 3. Agent with Webhooks

**File**: [`webhook_agent.py`](webhook_agent.py)

Demonstrates webhook-based agent notifications:
- Runs a FastAPI server to receive webhooks
- Registers with Contex using webhook URL
- Receives updates via HTTP POST
- HMAC signature verification

```bash
# Terminal 1: Start the webhook agent
python examples/webhook_agent.py

# Terminal 2: Publish data to trigger webhooks
python examples/publish_data.py
```

**When to use**:
- For serverless functions (AWS Lambda, Cloud Functions)
- When you don't want Redis dependencies
- For agents behind firewalls (webhook can be proxied)

---

### 4. Ad-hoc Queries

**File**: [`query_example.py`](query_example.py)

Shows how to query project data without registering an agent:
- One-off semantic queries
- Retrieve specific information
- No long-running connections required

```bash
# First, publish some data
python examples/publish_data.py

# Then query it
python examples/query_example.py
```

**When to use**:
- CLI tools that need project info
- One-off queries during development
- Testing semantic matching quality
- Interactive exploration

---

### 5. Updating Data

**File**: [`update_data.py`](update_data.py)

Demonstrates updating existing data:
- Modify coding standards
- Add new API endpoints
- Agents receive notifications automatically

```bash
# Terminal 1: Start an agent to see updates
python examples/agent_redis.py

# Terminal 2: Update data
python examples/update_data.py
```

**When to use**: When project data changes and agents need to be notified.

---

## Complete Workflow Example

Try this end-to-end workflow:

### Step 1: Publish Initial Data

```bash
python examples/publish_data.py
```

You should see:
```
Publishing coding standards...
  ✓ Status: 200
Publishing API endpoints...
  ✓ Status: 200
...
✓ All project data published successfully!
```

### Step 2: Start a Redis Agent

In a new terminal:

```bash
python examples/agent_redis.py
```

You should see:
```
✓ Agent registered successfully!
  Agent ID: code-generator
  ...

📥 Received Initial Context
===========================================

Need: 'code style guidelines and formatting rules'
  ✓ coding_standards
    Similarity: 0.85
    Data: {...}
```

### Step 3: Start a Webhook Agent

In another terminal:

```bash
python examples/webhook_agent.py
```

You should see similar output via webhook delivery.

### Step 4: Update Data

In a fourth terminal:

```bash
python examples/update_data.py
```

Watch the agent terminals - both should receive update notifications!

### Step 5: Query Data

```bash
python examples/query_example.py
```

You'll get matching data without needing to register an agent.

---

## Customizing Examples

### Change Project ID

Edit the `PROJECT_ID` variable at the top of each file:

```python
PROJECT_ID = "your-project-name"
```

### Adjust Agent Needs

Modify the `data_needs` list when registering agents:

```python
"data_needs": [
    "your custom need in natural language",
    "another need you want to match"
]
```

### Configure Matching Threshold

Set environment variable before starting Contex:

```bash
export SIMILARITY_THRESHOLD=0.6  # Higher = stricter matching
docker compose up -d
```

---

## Troubleshooting

### Connection Errors

```
✗ Could not connect to Contex at http://localhost:8001
```

**Solution**: Start Contex with `docker compose up -d`

### Redis Connection Failed

```
✗ Could not connect to Redis at localhost:6379
```

**Solution**: Check Redis is running:
```bash
docker compose ps
# Should show redis as healthy
```

### No Matches Found

If your agent receives no matches, the similarity threshold might be too high:

1. Lower the threshold:
   ```bash
   # In docker-compose.yml
   environment:
     - SIMILARITY_THRESHOLD=0.3
   ```

2. Restart Contex:
   ```bash
   docker compose restart contex
   ```

3. Use more descriptive data needs:
   ```python
   # Instead of:
   "data_needs": ["api"]

   # Try:
   "data_needs": ["API endpoints and authentication methods"]
   ```

### Webhook Not Receiving Updates

Check:
1. Webhook URL is accessible from Contex container
2. Use `host.docker.internal` for localhost webhooks
3. Check webhook server logs for errors
4. Verify HMAC signature if using secrets

---

## Building Your Own

Use these examples as templates for your own agents:

1. **Copy an example**: Start with `agent_redis.py` or `webhook_agent.py`
2. **Modify data needs**: Change what context your agent requires
3. **Add processing logic**: Implement `handle_update()` to process updates
4. **Customize behavior**: Add your agent's specific functionality

### Example: Custom Agent

```python
import asyncio
import json
import redis.asyncio as redis
import httpx

async def run_my_agent():
    # Register with specific needs
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8001/agents/register",
            json={
                "agent_id": "my-custom-agent",
                "project_id": "my-project",
                "data_needs": [
                    "deployment configuration and environment settings",
                    "monitoring and alerting rules"
                ]
            }
        )
        result = response.json()
        channel = result['notification_channel']

    # Listen for updates
    r = await redis.from_url("redis://localhost:6379")
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)

    async for message in pubsub.listen():
        if message["type"] == "message":
            update = json.loads(message["data"])

            # Your custom processing here!
            if update["type"] == "data_update":
                print(f"Processing update: {update['data_key']}")
                # Do something with the update...

if __name__ == "__main__":
    asyncio.run(run_my_agent())
```

---

## Next Steps

- **Read the [Quick Start Guide](../QUICKSTART.md)** for detailed walkthrough
- **Check the [Main README](../README.md)** for API documentation
- **Review [Docker Setup](../README_DOCKER.md)** for production deployment
- **Join the [Discord](https://discord.gg/contex)** to share your use cases!

---

**Happy building!** 🚀
