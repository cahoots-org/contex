# Contex Python SDK

Official Python client for [Contex](https://github.com/cahoots-org/contex) - Semantic context routing for AI agents.

## Installation

```bash
pip install contex-python
```

## Quick Start

### Async Client (Recommended)

```python
from contex import ContexAsyncClient

async def main():
    async with ContexAsyncClient(
        url="http://localhost:8001",
        api_key="ck_your_api_key_here"
    ) as client:
        # Publish data
        await client.publish(
            project_id="my-app",
            data_key="coding_standards",
            data={
                "style": "PEP 8",
                "max_line_length": 100,
                "quotes": "double"
            }
        )
        
        # Register agent
        response = await client.register_agent(
            agent_id="code-reviewer",
            project_id="my-app",
            data_needs=[
                "coding standards and style guidelines",
                "testing requirements and coverage goals"
            ]
        )
        
        print(f"Matched needs: {response.matched_needs}")
        print(f"Notification channel: {response.notification_channel}")
        
        # Query for data
        results = await client.query(
            project_id="my-app",
            query="authentication configuration"
        )
        
        for result in results.results:
            print(f"{result.data_key}: {result.data}")

import asyncio
asyncio.run(main())
```

### Sync Client

```python
from contex import ContexClient

client = ContexClient(
    url="http://localhost:8001",
    api_key="ck_your_api_key_here"
)

# Publish data
client.publish(
    project_id="my-app",
    data_key="config",
    data={"env": "prod", "debug": False}
)

# Register agent
response = client.register_agent(
    agent_id="my-agent",
    project_id="my-app",
    data_needs=["configuration", "secrets"]
)
```

## Features

- ✅ **Async & Sync**: Both async and synchronous interfaces
- ✅ **Type Hints**: Full type annotations with Pydantic models
- ✅ **Error Handling**: Comprehensive exception hierarchy
- ✅ **Retry Logic**: Automatic retries with exponential backoff
- ✅ **Rate Limiting**: Built-in rate limit handling
- ✅ **Authentication**: API key authentication support

## API Reference

### Client Initialization

```python
client = ContexAsyncClient(
    url="http://localhost:8001",  # Contex server URL
    api_key="ck_...",              # API key for authentication
    timeout=30.0,                  # Request timeout in seconds
    max_retries=3,                 # Maximum number of retries
)
```

### Publishing Data

```python
await client.publish(
    project_id="my-app",           # Project identifier
    data_key="unique-key",         # Unique key for this data
    data={"any": "json"},          # Data payload
    data_format="json",            # Format: json, yaml, toml, text
    metadata={"tags": ["prod"]},   # Optional metadata
)
```

### Registering Agents

```python
response = await client.register_agent(
    agent_id="agent-1",                    # Unique agent ID
    project_id="my-app",                   # Project ID
    data_needs=["config", "secrets"],      # Data needs (natural language)
    notification_method="redis",           # redis or webhook
    webhook_url="https://...",             # Optional webhook URL
    webhook_secret="secret",               # Optional webhook secret
    last_seen_sequence="0",                # Last seen sequence
)
```

### Querying Data

```python
results = await client.query(
    project_id="my-app",
    query="authentication settings",
    max_results=10,
)

for result in results.results:
    print(f"{result.data_key}: {result.similarity_score}")
```

### API Key Management

```python
# Create API key
key_response = await client.create_api_key(name="production-key")
print(f"API Key: {key_response.key}")  # Store this securely!

# List keys
keys = await client.list_api_keys()

# Revoke key
await client.revoke_api_key(key_id="key-123")
```

### Health Checks

```python
# Comprehensive health
health = await client.health()

# Readiness check
ready = await client.ready()

# Rate limit status
rate_limit = await client.rate_limit_status()
print(f"Remaining: {rate_limit.remaining}/{rate_limit.limit}")
```

## Exception Handling

```python
from contex import (
    ContexError,
    AuthenticationError,
    RateLimitError,
    ValidationError,
    NotFoundError,
    ServerError,
)

try:
    await client.publish(...)
except AuthenticationError:
    print("Invalid API key")
except RateLimitError as e:
    print(f"Rate limited. Retry after {e.retry_after} seconds")
except ValidationError as e:
    print(f"Validation error: {e}")
except NotFoundError:
    print("Resource not found")
except ServerError:
    print("Server error")
except ContexError as e:
    print(f"Contex error: {e}")
```

## Development

### Setup

```bash
cd sdk/python
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black contex/
ruff check contex/
mypy contex/
```

## Examples

See the [examples](examples/) directory for more usage examples:

- `basic_usage.py` - Basic publish and query
- `agent_registration.py` - Agent registration and updates
- `webhook_agent.py` - Webhook-based agent
- `error_handling.py` - Error handling patterns
- `batch_operations.py` - Batch publishing

## License

MIT License - see [LICENSE](LICENSE) for details.

## Links

- [Documentation](https://contex.readthedocs.io)
- [GitHub](https://github.com/cahoots-org/contex)
- [PyPI](https://pypi.org/project/contex-python/)
- [Issues](https://github.com/cahoots-org/contex/issues)
