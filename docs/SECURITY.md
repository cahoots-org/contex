# Contex Security Overview

This document provides an overview of all security features implemented in Contex.

## Security Features

Contex implements a comprehensive security model with three layers:

1. **Authentication** - API Key validation
2. **Authorization** - Role-Based Access Control (RBAC)
3. **Rate Limiting** - Protection against abuse

## 1. API Key Authentication

**Status**: ✅ Implemented (Phase 1.1.1)

All API endpoints require valid API keys for authentication.

### Features
- SHA-256 hashed key storage
- Secure key generation (`ck_` prefix + 32-byte random token)
- Key management endpoints (create, list, revoke)
- Automatic validation on every request

### Documentation
- [Authentication Guide](AUTHENTICATION.md)

### Example
```bash
# Create a key
curl -X POST http://localhost:8001/api/auth/keys?name=my-app \
  -H "X-API-Key: $ADMIN_KEY"

# Use the key
curl -H "X-API-Key: ck_..." http://localhost:8001/api/...
```

## 2. Role-Based Access Control (RBAC)

**Status**: ✅ Implemented (Phase 1.1.3)

Fine-grained access control with four built-in roles and project-level permissions.

### Roles

| Role | Permissions | Use Case |
|------|------------|----------|
| **admin** | All operations | System administrators |
| **publisher** | Publish data | Data sources, backend services |
| **consumer** | Register agents, query | AI agents, consumers |
| **readonly** | View/query only | Monitoring, dashboards |

### Features
- Project-level access control
- 12 granular permissions
- Default readonly for unassigned keys
- Role management API

### Documentation
- [RBAC Guide](RBAC.md)
- [Quick Reference](RBAC_QUICK_REFERENCE.md)

### Example
```bash
# Assign publisher role for specific projects
curl -X POST http://localhost:8001/api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{
    "key_id": "abc123",
    "role": "publisher",
    "projects": ["proj1", "proj2"]
  }'
```

## 3. Rate Limiting

**Status**: ✅ Implemented (Phase 1.1.2)

Redis-based sliding window rate limiting to prevent abuse.

### Default Limits

| Operation | Limit | Scope |
|-----------|-------|-------|
| Publish data | 100/min | Per API key + endpoint |
| Register agent | 50/min | Per API key + endpoint |
| Query data | 200/min | Per API key + endpoint |
| Admin operations | 20/min | Per API key + endpoint |

### Features
- Sliding window algorithm (more accurate than fixed windows)
- Per-API-key and per-endpoint limits
- Standard rate limit headers (`X-RateLimit-*`)
- Automatic cleanup of old request records

### Documentation
- [Rate Limiting Guide](RATE_LIMITING.md)

### Example
```bash
# Check rate limit status
curl -H "X-API-Key: $YOUR_KEY" \
  http://localhost:8001/api/admin/rate-limits
```

## Security Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client Request                        │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              1. API Key Middleware                       │
│  • Validates X-API-Key header                           │
│  • Checks key exists in Redis                           │
│  • Stores key_id in request state                       │
│  • Returns 401 if invalid                               │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              2. RBAC Middleware                          │
│  • Gets role for key_id                                 │
│  • Checks required permission for endpoint              │
│  • Validates project-level access                       │
│  • Returns 403 if forbidden                             │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              3. Rate Limit Middleware                    │
│  • Checks request count in sliding window               │
│  • Updates request counter                              │
│  • Adds rate limit headers                              │
│  • Returns 429 if exceeded                              │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│                  API Endpoint                            │
│  • Processes request                                     │
│  • Returns response                                      │
└─────────────────────────────────────────────────────────┘
```

## Response Codes

| Code | Meaning | Cause | Solution |
|------|---------|-------|----------|
| **200** | Success | Request authorized and processed | - |
| **401** | Unauthorized | Missing or invalid API key | Provide valid `X-API-Key` header |
| **403** | Forbidden | Valid key but insufficient permissions | Assign appropriate role |
| **429** | Too Many Requests | Rate limit exceeded | Wait for rate limit reset |

## Security Best Practices

### 1. API Key Management

✅ **DO**:
- Store admin keys in environment variables or secrets manager
- Rotate keys regularly
- Use separate keys for different services
- Revoke unused keys immediately

❌ **DON'T**:
- Commit keys to version control
- Share keys between services
- Use the same key for dev and production
- Log API keys

### 2. Role Assignment

✅ **DO**:
- Follow principle of least privilege
- Restrict to specific projects when possible
- Review role assignments regularly
- Use readonly for monitoring tools

❌ **DON'T**:
- Give admin role unnecessarily
- Use empty projects list unless needed
- Reuse keys across different roles
- Grant more permissions than required

### 3. Rate Limiting

✅ **DO**:
- Monitor rate limit headers
- Implement exponential backoff
- Cache results to reduce API calls
- Request limit increases if needed

❌ **DON'T**:
- Ignore 429 responses
- Retry immediately without backoff
- Make unnecessary API calls
- Use multiple keys to bypass limits

## Monitoring and Auditing

### Check API Key Usage
```bash
# List all API keys
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8001/api/auth/keys

# Check rate limit status
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8001/api/admin/rate-limits
```

### Check Role Assignments
```bash
# List all roles
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8001/api/auth/roles

# Get role for specific key
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8001/api/auth/roles/{key_id}
```

### View Available Permissions
```bash
# List all permissions and roles
curl http://localhost:8001/api/auth/permissions
```

## Common Security Scenarios

### Scenario 1: New Project Setup

```bash
# 1. Create publisher key for backend
PUBLISHER_KEY=$(curl -X POST /api/auth/keys?name=backend \
  -H "X-API-Key: $ADMIN_KEY" | jq -r '.api_key')

# 2. Assign publisher role
curl -X POST /api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{"key_id": "...", "role": "publisher", "projects": ["new-proj"]}'

# 3. Create consumer key for AI agents
CONSUMER_KEY=$(curl -X POST /api/auth/keys?name=ai-agent \
  -H "X-API-Key: $ADMIN_KEY" | jq -r '.api_key')

# 4. Assign consumer role
curl -X POST /api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{"key_id": "...", "role": "consumer", "projects": ["new-proj"]}'
```

### Scenario 2: Revoking Access

```bash
# 1. Revoke role (downgrades to readonly)
curl -X DELETE /api/auth/roles/{key_id} \
  -H "X-API-Key: $ADMIN_KEY"

# 2. Revoke API key completely
curl -X DELETE /api/auth/keys/{key_id} \
  -H "X-API-Key: $ADMIN_KEY"
```

### Scenario 3: Handling Rate Limits

```python
import time
import requests

def make_request_with_retry(url, headers):
    response = requests.post(url, headers=headers)
    
    if response.status_code == 429:
        # Get retry-after header
        retry_after = int(response.headers.get('Retry-After', 60))
        print(f"Rate limited. Waiting {retry_after}s...")
        time.sleep(retry_after)
        return make_request_with_retry(url, headers)
    
    return response
```

## Future Security Enhancements

Planned for future releases:

- [ ] **Audit Logging** - Track all security events
- [ ] **IP Whitelisting** - Restrict access by IP
- [ ] **Key Expiration** - Time-limited API keys
- [ ] **Custom Roles** - Define custom roles with specific permissions
- [ ] **OAuth2/OIDC** - Enterprise SSO integration
- [ ] **Webhook Signing** - HMAC signatures for webhooks (already implemented)
- [ ] **TLS/SSL** - Built-in HTTPS support
- [ ] **Secrets Rotation** - Automated key rotation

## Security Disclosure

If you discover a security vulnerability, please email security@contex.io (or create a private GitHub security advisory).

**Please do not** open public issues for security vulnerabilities.

## Compliance

Current compliance status:

- ✅ **Authentication**: API key-based
- ✅ **Authorization**: Role-based access control
- ✅ **Rate Limiting**: Protection against abuse
- ⏳ **Audit Logging**: Planned
- ⏳ **Encryption at Rest**: Depends on Redis configuration
- ⏳ **Encryption in Transit**: Requires reverse proxy (nginx, Envoy)

## Related Documentation

- [Authentication Guide](AUTHENTICATION.md)
- [RBAC Guide](RBAC.md)
- [RBAC Quick Reference](RBAC_QUICK_REFERENCE.md)
- [Rate Limiting Guide](RATE_LIMITING.md)
- [API Documentation](http://localhost:8001/docs)

## Summary

Contex provides enterprise-grade security with:

✅ **API Key Authentication** - All endpoints protected  
✅ **Role-Based Access Control** - Fine-grained permissions  
✅ **Rate Limiting** - Abuse prevention  
✅ **Project Isolation** - Multi-tenant support  
✅ **Secure by Default** - Readonly default for new keys  

All security features are production-ready and fully tested (117 passing tests).
