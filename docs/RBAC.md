# Role-Based Access Control (RBAC)

## Overview

When authentication is enabled (`AUTH_ENABLED=true`), every MCP tool call is
authorized against the caller's role. Each API key is assigned one role, and
each role grants a fixed set of permissions. Authorization is fail-closed: a
call is allowed only if the caller's role includes the required permission (and,
where applicable, the target project is in the key's allowed-project list).

When authentication is disabled (the default, for local development), all calls
are permitted — see the auth-posture note in the [README](../README.md).

## Roles

| Role | Intended for | Permissions |
|------|--------------|-------------|
| `admin` | Operators / bootstrap | Every permission |
| `publisher` | Data sources that write context | `publish_data`, `view_project_data`, `view_project_events`, `view_version_history` |
| `consumer` | Agents that read context | `query_data`, `view_project_data`, `view_project_events`, `view_version_history` |
| `readonly` | Dashboards / auditors | `query_data`, `view_project_data`, `view_project_events`, `view_version_history` |

Keys default to `readonly` when no role is assigned.

## Permissions

| Permission | Grants |
|------------|--------|
| `publish_data` | Publish / update context (`contex_publish`, `contex_publish_batch`) |
| `query_data` | Query context and create subscriptions (`contex_query`, `contex_create_subscription`) |
| `view_project_data` | Read a project's stored context |
| `view_project_events` | Read a project's event stream |
| `view_version_history` | Read a data key's version history |

## Project scoping

A role assignment may be restricted to specific projects. An empty project list
means "all projects". A permission check for a specific project passes only when
the project is in the key's allowed list; global checks (no project) pass when
the role holds the permission.

## Role assignment

Roles are assigned server-side (`src.core.rbac.assign_role`) during
provisioning/bootstrap. There is no client-facing role-management API in this
version — the REST admin surface was removed in favor of the MCP-native
interface. Assign roles as part of your deployment/provisioning flow.

## Best practices

- **Least privilege:** give data sources `publisher`, agents `consumer`, and
  dashboards `readonly`. Reserve `admin` for operators.
- **Separate keys per service** so a leak is contained and revocation is
  surgical.
- **Scope keys to projects** they actually need rather than granting all-project
  access.
