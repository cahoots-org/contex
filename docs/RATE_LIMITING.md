# Rate Limiting

Contex includes built-in rate limiting to protect the API from abuse and ensure fair resource allocation.

## Overview

Rate limiting is enforced using a **Redis-based sliding window algorithm** that tracks requests per API key and endpoint.

## Rate Limits

Default limits (requests per minute):

| Endpoint Type | Limit | Description |
|--------------|-------|-------------|
| `/api/publish` | 100/min | Data publishing operations |
| `/api/register` | 50/min | Agent registration |
| `/api/query` | 200/min | Ad-hoc queries |
| `/auth/*` | 20/min | Authentication operations |
| `/admin/*` | 20/min | Admin operations |
| Other endpoints | 60/min | Default limit |

## Response Headers

All API responses include rate limit headers:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1234567890
```

- `X-RateLimit-Limit`: Maximum requests allowed in the window
- `X-RateLimit-Remaining`: Requests remaining in current window
- `X-RateLimit-Reset`: Unix timestamp when the window resets

## Rate Limit Exceeded

When you exceed the rate limit, you'll receive a `429 Too Many Requests` response:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Rate limit exceeded. Try again in 60 seconds.",
  "limit": 100,
  "reset": 1234567890
}
```

The response also includes a `Retry-After` header indicating how many seconds to wait.

## Checking Rate Limit Status

You can check your current rate limit status:

```bash
curl -H "X-API-Key: your-api-key" \
  http://localhost:8001/api/admin/rate-limits
```

Response:

```json
{
  "api_key_prefix": "sk_test",
  "limits": {
    "/api/publish": {
      "limit": 100,
      "remaining": 95,
      "reset": 1234567890,
      "allowed": true
    },
    "/api/query": {
      "limit": 200,
      "remaining": 198,
      "reset": 1234567890,
      "allowed": true
    }
  }
}
```

## Implementation Details

### Sliding Window Algorithm

The rate limiter uses Redis sorted sets to implement a sliding window:

1. Each request is recorded with a timestamp
2. Old requests outside the window are removed
3. Current request count is checked against the limit
4. If under limit, the request is allowed and recorded

This provides more accurate rate limiting than fixed windows, as it considers the exact timing of requests.

### Per-Endpoint Limits

Rate limits are enforced per combination of:
- **API Key**: Each key has independent limits
- **Endpoint**: Different endpoints have different limits
- **Project ID** (when applicable): Project-level isolation

### Exemptions

The following endpoints are exempt from rate limiting:
- `/health` - Health checks
- `/` - Root redirect
- `/docs` - API documentation
- `/openapi.json` - OpenAPI spec
- `/redoc` - ReDoc documentation

## Best Practices

1. **Monitor Headers**: Always check rate limit headers to avoid hitting limits
2. **Implement Backoff**: Use exponential backoff when you receive 429 responses
3. **Batch Operations**: Group multiple operations when possible
4. **Cache Results**: Cache query results to reduce API calls
5. **Request Increases**: Contact support if you need higher limits

## Example: Handling Rate Limits

```python
import time
import requests

def make_request_with_retry(url, headers, max_retries=3):
    for attempt in range(max_retries):
        response = requests.get(url, headers=headers)
        
        if response.status_code == 429:
            # Rate limited - wait and retry
            retry_after = int(response.headers.get('Retry-After', 60))
            print(f"Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            continue
        
        # Check remaining requests
        remaining = int(response.headers.get('X-RateLimit-Remaining', 0))
        if remaining < 10:
            print(f"Warning: Only {remaining} requests remaining")
        
        return response
    
    raise Exception("Max retries exceeded")
```

## Configuration

Rate limits are currently hardcoded but can be customized by modifying `src/core/rate_limiter.py`:

```python
class RateLimitConfig:
    PUBLISH_DATA = 100  # requests per minute
    REGISTER_AGENT = 50
    QUERY = 200
    ADMIN = 20
    DEFAULT = 60
```

Future versions will support per-API-key custom limits via the admin API.
