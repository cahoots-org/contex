# RBAC Quick Reference

## Quick Start

### 1. Create an API Key (Admin Only)
```bash
curl -X POST "http://localhost:8001/api/auth/keys?name=my-key" \
  -H "X-API-Key: $ADMIN_KEY"
```

### 2. Assign a Role
```bash
curl -X POST http://localhost:8001/api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_id": "KEY_ID_FROM_STEP_1",
    "role": "publisher",
    "projects": ["proj1"]
  }'
```

### 3. Use the Key
```bash
curl -X POST http://localhost:8001/api/data/publish \
  -H "X-API-Key: KEY_FROM_STEP_1" \
  -d '{"project_id": "proj1", "data_key": "test", "data": {}}'
```

## Roles at a Glance

| Role | Can Publish | Can Query | Can Register Agents | Can Manage Keys | Use Case |
|------|-------------|-----------|---------------------|-----------------|----------|
| **admin** | ✅ | ✅ | ✅ | ✅ | System administrators |
| **publisher** | ✅ | ❌ | ❌ | ❌ | Data sources |
| **consumer** | ❌ | ✅ | ✅ | ❌ | AI agents |
| **readonly** | ❌ | ✅ | ❌ | ❌ | Monitoring |

## Common Commands

### List All Roles
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8001/api/auth/roles
```

### Get Role for Specific Key
```bash
curl -H "X-API-Key: $ADMIN_KEY" \
  http://localhost:8001/api/auth/roles/{key_id}
```

### Update a Role
```bash
curl -X POST http://localhost:8001/api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{"key_id": "abc123", "role": "consumer", "projects": []}'
```

### Revoke a Role
```bash
curl -X DELETE http://localhost:8001/api/auth/roles/{key_id} \
  -H "X-API-Key: $ADMIN_KEY"
```

### View All Permissions
```bash
curl http://localhost:8001/api/auth/permissions
```

## Project Scoping

### All Projects (Admin)
```json
{
  "key_id": "admin-key",
  "role": "admin",
  "projects": []
}
```

### Specific Projects
```json
{
  "key_id": "publisher-key",
  "role": "publisher",
  "projects": ["proj1", "proj2", "proj3"]
}
```

## Error Handling

### 403 Forbidden
```json
{
  "error": "forbidden",
  "message": "Your role 'publisher' does not have permission...",
  "required_permission": "register_agent",
  "your_role": "publisher"
}
```

**Solution**: Assign correct role or add project to allowed list.

## Best Practices

1. **Least Privilege**: Assign minimum necessary role
2. **Project Isolation**: Restrict to specific projects when possible
3. **Separate Keys**: One key per service/purpose
4. **Regular Audits**: Review roles periodically
5. **Secure Admin Keys**: Store securely, rotate regularly

## Full Documentation

See [docs/RBAC.md](RBAC.md) for complete documentation.
