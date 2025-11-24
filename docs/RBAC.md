# Role-Based Access Control (RBAC)

Contex includes a flexible Role-Based Access Control system to manage permissions for API keys.

## Overview

RBAC allows you to:
- Assign roles to API keys
- Control access to specific operations
- Restrict access to specific projects
- Enforce the principle of least privilege

## Roles

Contex defines four built-in roles:

### Admin
**Full access to all operations and projects**

Permissions:
- ✅ Publish data
- ✅ Query data
- ✅ Register agents
- ✅ Manage agents (list, delete)
- ✅ Create/revoke API keys
- ✅ Manage roles
- ✅ View rate limits
- ✅ View all project data

**Use case:** System administrators, DevOps teams

### Publisher
**Can publish data to assigned projects**

Permissions:
- ✅ Publish data
- ✅ View project data
- ✅ View project events
- ❌ Register agents
- ❌ Manage API keys
- ❌ Manage roles

**Use case:** Data producers, backend services that publish context

### Consumer
**Can register agents and query data**

Permissions:
- ✅ Register agents
- ✅ Query data
- ✅ List agents
- ✅ Delete agents
- ✅ View project data
- ❌ Publish data
- ❌ Manage API keys

**Use case:** AI agents, consumers of context data

### Readonly
**Can only view and query data**

Permissions:
- ✅ Query data
- ✅ View project data
- ✅ List agents
- ❌ Publish data
- ❌ Register agents
- ❌ Delete agents
- ❌ Manage API keys

**Use case:** Monitoring tools, read-only dashboards

## Project-Level Permissions

Roles can be scoped to specific projects:

```bash
# Assign publisher role for specific projects
curl -X POST http://localhost:8001/api/auth/roles \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "key_id": "abc123",
    "role": "publisher",
    "projects": ["proj1", "proj2"]
  }'
```

- **Empty projects list** (`[]`): Access to ALL projects
- **Specific projects** (`["proj1", "proj2"]`): Access only to listed projects

## Usage

### 1. Create an API Key

First, create an API key (requires admin role):

```bash
curl -X POST http://localhost:8001/api/auth/keys?name=my-publisher-key \
  -H "X-API-Key: admin-key"
```

Response:
```json
{
  "api_key": "ck_...",
  "key_id": "abc123",
  "name": "my-publisher-key"
}
```

### 2. Assign a Role

Assign a role to the API key:

```bash
curl -X POST http://localhost:8001/api/auth/roles \
  -H "X-API-Key: admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "key_id": "abc123",
    "role": "publisher",
    "projects": ["proj1"]
  }'
```

Response:
```json
{
  "key_id": "abc123",
  "role": "publisher",
  "projects": ["proj1"]
}
```

### 3. Use the API Key

The API key now has publisher permissions for `proj1`:

```bash
# This works - publishing to proj1
curl -X POST http://localhost:8001/api/data/publish \
  -H "X-API-Key: ck_..." \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj1",
    "data_key": "config",
    "data": {"setting": "value"}
  }'

# This fails - publishing to proj2 (not authorized)
curl -X POST http://localhost:8001/api/data/publish \
  -H "X-API-Key: ck_..." \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "proj2",
    "data_key": "config",
    "data": {"setting": "value"}
  }'
```

Error response (403 Forbidden):
```json
{
  "error": "forbidden",
  "message": "Your role 'publisher' does not have permission to perform this action",
  "required_permission": "publish_data",
  "your_role": "publisher"
}
```

## Management Endpoints

### List All Roles

```bash
GET /api/auth/roles
```

Response:
```json
[
  {
    "key_id": "abc123",
    "role": "publisher",
    "projects": ["proj1"]
  },
  {
    "key_id": "def456",
    "role": "consumer",
    "projects": []
  }
]
```

### Get Role for Specific Key

```bash
GET /api/auth/roles/{key_id}
```

Response:
```json
{
  "key_id": "abc123",
  "role": "publisher",
  "projects": ["proj1"]
}
```

### Update Role

To update a role, simply assign a new role (overwrites existing):

```bash
POST /api/auth/roles
{
  "key_id": "abc123",
  "role": "consumer",
  "projects": ["proj1", "proj2"]
}
```

### Revoke Role

```bash
DELETE /api/auth/roles/{key_id}
```

After revocation, the key defaults to `readonly` role.

### List All Permissions

View all available roles and their permissions:

```bash
GET /api/auth/permissions
```

Response:
```json
{
  "roles": {
    "admin": {
      "permissions": ["publish_data", "query_data", "register_agent", ...]
    },
    "publisher": {
      "permissions": ["publish_data", "view_project_data", ...]
    },
    ...
  },
  "all_permissions": ["publish_data", "query_data", ...]
}
```

## Permission Matrix

| Operation | Admin | Publisher | Consumer | Readonly |
|-----------|-------|-----------|----------|----------|
| Publish data | ✅ | ✅ | ❌ | ❌ |
| Query data | ✅ | ❌ | ✅ | ✅ |
| Register agent | ✅ | ❌ | ✅ | ❌ |
| List agents | ✅ | ❌ | ✅ | ✅ |
| Delete agent | ✅ | ❌ | ✅ | ❌ |
| View project data | ✅ | ✅ | ✅ | ✅ |
| View project events | ✅ | ✅ | ✅ | ✅ |
| Create API key | ✅ | ❌ | ❌ | ❌ |
| Revoke API key | ✅ | ❌ | ❌ | ❌ |
| Manage roles | ✅ | ❌ | ❌ | ❌ |
| View rate limits | ✅ | ❌ | ❌ | ❌ |

## Default Behavior

- **New API keys** without assigned roles default to `readonly`
- **Readonly role** has minimal permissions (view-only)
- **Project restrictions** apply to all roles except admin with empty projects list

## Best Practices

### 1. Principle of Least Privilege

Assign the minimum role necessary:

```bash
# Good: Publisher for data sources
curl -X POST /api/auth/roles -d '{
  "key_id": "backend-service",
  "role": "publisher",
  "projects": ["production"]
}'

# Good: Consumer for AI agents
curl -X POST /api/auth/roles -d '{
  "key_id": "ai-agent",
  "role": "consumer",
  "projects": ["production"]
}'
```

### 2. Project Isolation

Restrict keys to specific projects:

```bash
# Development key - only dev projects
{
  "key_id": "dev-key",
  "role": "admin",
  "projects": ["dev", "staging"]
}

# Production key - only production
{
  "key_id": "prod-key",
  "role": "publisher",
  "projects": ["production"]
}
```

### 3. Separate Keys for Different Services

Create separate API keys for different services:

```bash
# Backend service (publishes data)
POST /api/auth/keys?name=backend-service
POST /api/auth/roles {"key_id": "...", "role": "publisher"}

# AI agent (consumes data)
POST /api/auth/keys?name=ai-agent
POST /api/auth/roles {"key_id": "...", "role": "consumer"}

# Monitoring (read-only)
POST /api/auth/keys?name=monitoring
POST /api/auth/roles {"key_id": "...", "role": "readonly"}
```

### 4. Regular Audits

Periodically review role assignments:

```bash
# List all roles
GET /api/auth/roles

# Review and revoke unused keys
DELETE /api/auth/keys/{key_id}
DELETE /api/auth/roles/{key_id}
```

### 5. Admin Key Security

- Store admin keys securely (environment variables, secrets manager)
- Limit admin key distribution
- Rotate admin keys regularly
- Never commit admin keys to version control

## Example Workflows

### Setting Up a New Project

```bash
# 1. Create publisher key for backend
PUBLISHER_KEY=$(curl -X POST /api/auth/keys?name=backend-publisher \
  -H "X-API-Key: $ADMIN_KEY" | jq -r '.api_key')

curl -X POST /api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{
    "key_id": "'$(curl -X POST /api/auth/keys?name=backend-publisher \
      -H "X-API-Key: $ADMIN_KEY" | jq -r '.key_id')'",
    "role": "publisher",
    "projects": ["new-project"]
  }'

# 2. Create consumer key for AI agents
CONSUMER_KEY=$(curl -X POST /api/auth/keys?name=ai-consumer \
  -H "X-API-Key: $ADMIN_KEY" | jq -r '.api_key')

curl -X POST /api/auth/roles \
  -H "X-API-Key: $ADMIN_KEY" \
  -d '{
    "key_id": "'$(curl -X POST /api/auth/keys?name=ai-consumer \
      -H "X-API-Key: $ADMIN_KEY" | jq -r '.key_id')'",
    "role": "consumer",
    "projects": ["new-project"]
  }'

# 3. Backend publishes data
curl -X POST /api/data/publish \
  -H "X-API-Key: $PUBLISHER_KEY" \
  -d '{"project_id": "new-project", "data_key": "config", "data": {}}'

# 4. AI agent registers and receives data
curl -X POST /api/agents/register \
  -H "X-API-Key: $CONSUMER_KEY" \
  -d '{"agent_id": "agent1", "project_id": "new-project", "data_needs": ["config"]}'
```

## Troubleshooting

### 403 Forbidden Errors

If you receive a 403 error:

1. **Check your role**:
   ```bash
   curl -H "X-API-Key: $YOUR_KEY" /api/auth/roles/{your_key_id}
   ```

2. **Verify project access**:
   - Ensure the project is in your `projects` list
   - Or ensure `projects` is empty (all projects)

3. **Check required permission**:
   - The error message includes `required_permission`
   - Verify your role has that permission (see Permission Matrix)

### Role Not Taking Effect

- Roles are checked on every request (no caching)
- Ensure you're using the correct API key
- Verify the role was assigned successfully

### Default Readonly Behavior

- Keys without assigned roles default to `readonly`
- Explicitly assign a role after creating a key
- Use `GET /api/auth/roles/{key_id}` to verify

## Security Considerations

1. **RBAC is enforced at the API layer** - Direct Redis access bypasses RBAC
2. **Project IDs are extracted from requests** - Ensure correct project_id in payloads
3. **Admin role is powerful** - Limit admin key distribution
4. **Roles are stored in Redis** - Secure your Redis instance
5. **No role inheritance** - Each key has exactly one role

## Future Enhancements

Planned improvements:
- Custom roles with granular permissions
- Role templates
- Time-based role assignments
- Audit logging for role changes
- Role-based rate limits
