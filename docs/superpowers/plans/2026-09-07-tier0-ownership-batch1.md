# Tier 0 Ownership Checks — Batch 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the tenant/project IDOR gaps that the Wave D `Identity` foundation now makes expressible — API-key tenant-scoping (#43), cross-project ownership on data routes (#54), admin-cleanup ownership (#55), the `/sandbox/subscribe` project-scope check — and remove the dead `require_admin_permission` guard (#39 remnant).

**Architecture:** A single `check_project_access(identity, project_id, tenant_mgr, *, create_if_absent)` helper enforces project→tenant ownership, gated so it is a **no-op unless multi-tenancy is on AND the caller carries a tenant** (single-tenant/demo unaffected). Routes that need it take `identity: Identity = Depends(require(P))` (instead of a bare `dependencies=[...]`) and call the helper. API-key routes pass `identity.tenant_id` to the already-tenant-aware service functions.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Postgres+pgvector, pytest (live `pgvector/pgvector:pg16` + Redis).

**Spec basis:** Wave D foundation (`docs/superpowers/specs/2026-09-05-fail-closed-authz-foundation-design.md`, §Non-goals lists these as fast-follows). Ownership-model recon: project→tenant lives in `TenantProject`, resolvable via `TenantManager.get_project_tenant()`; `APIKey.tenant_id` exists; there is no Projects table (project_id is a free-form string).

## Global Constraints

- **No DB migration.** Alembic head stays `006`. Batch 2 (#42/#73 subscriptions) is a separate plan.
- **Ownership check is gated:** no-op when `not MULTI_TENANT_ENABLED` OR `identity.tenant_id is None`. This preserves single-tenant and auth-off/demo behavior exactly.
- **Decisions locked (Rob, 2026-09-07):** (1) ownership active only when multi-tenant + identity has a tenant; (2) first publish to an unowned project auto-binds it to the caller's tenant; reads to an unowned project are denied.
- **Commit structure:** discrete single-purpose commits, each CI-green (TDD: test commit may precede impl). Work on branch `worktree-tier0-ownership`; never push or open the PR until asked.
- **Code style (Rob):** imports top-level (no inline); no meta/historical comments; concise docstrings; generic client-facing error text ("Forbidden"), never leak internals.
- **Tests:** `export DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"`. No new failures in `tests/` (~6 `sdk/python/tests` failures are known-unrelated).

## File Structure

- **New:** `src/core/ownership.py` (the helper), `tests/test_ownership.py`, `tests/test_tenant_isolation.py` (end-to-end matrix).
- **Modify:** `src/api/routes.py` (#54 data routes, #55 cleanup, #43 key routes), `src/core/auth.py` (#43 revoke tenant check), `src/web/routes.py` (`/subscribe` ownership), and `src/api/{tenant_routes,webhook_routes,service_account_routes,audit_routes}.py` (remove `require_admin_permission`).

---

## Task 1: `check_project_access` helper

**Files:** Create `src/core/ownership.py`; Test `tests/test_ownership.py`.

**Interfaces — Produces:**
`async def check_project_access(identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ownership.py
import pytest
from unittest.mock import AsyncMock
from fastapi import HTTPException

from src.core import ownership
from src.core.ownership import check_project_access
from src.core.identity import Identity
from src.core.rbac import Permission


def _ident(tenant_id, projects=()):
    return Identity(key_id="k", scopes=frozenset({Permission.PUBLISH_DATA}),
                    tenant_id=tenant_id, projects=tuple(projects), role=None)


@pytest.mark.asyncio
async def test_noop_when_multitenant_off(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", False)
    mgr = AsyncMock()
    await check_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)
    mgr.get_project_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_noop_when_identity_has_no_tenant(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock()
    await check_project_access(_ident(None), "p1", mgr, create_if_absent=False)
    mgr.get_project_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_read_denies_unowned_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    with pytest.raises(HTTPException) as e:
        await check_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_read_denies_other_tenant_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t2"
    with pytest.raises(HTTPException) as e:
        await check_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_read_allows_owned_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t1"
    await check_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_publish_binds_new_project(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    await check_project_access(_ident("t1"), "pnew", mgr, create_if_absent=True)
    mgr.add_project.assert_awaited_once_with("t1", "pnew")


@pytest.mark.asyncio
async def test_key_project_scope_denies_out_of_scope(monkeypatch):
    monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)
    mgr = AsyncMock()
    with pytest.raises(HTTPException) as e:
        await check_project_access(_ident("t1", projects=["allowed"]), "other", mgr, create_if_absent=True)
    assert e.value.status_code == 403
    mgr.get_project_tenant.assert_not_called()  # scope check short-circuits
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_ownership.py -v` → module missing.

- [ ] **Step 3: Implement**

```python
# src/core/ownership.py
"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from fastapi import HTTPException

from src.core.identity import Identity
from src.core.tenant_middleware import MULTI_TENANT_ENABLED


async def check_project_access(
    identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> None:
    """Enforce that `project_id` belongs to the caller's tenant.

    No-op when multi-tenancy is off or the caller carries no tenant. When
    `create_if_absent` is true (publish paths), an unowned project is bound to
    the caller's tenant; otherwise an unowned or cross-tenant project is denied.
    """
    if not MULTI_TENANT_ENABLED or identity.tenant_id is None:
        return
    if not identity.has_project(project_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    owner = await tenant_mgr.get_project_tenant(project_id)
    if owner is None:
        if create_if_absent:
            await tenant_mgr.add_project(identity.tenant_id, project_id)
            return
        raise HTTPException(status_code=403, detail="Forbidden")
    if owner != identity.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
```

- [ ] **Step 4: Run** — `pytest tests/test_ownership.py -v` → all pass.
- [ ] **Step 5: Commit** — `feat(authz): project→tenant ownership helper (gated on multi-tenant)`.

---

## Task 2: Apply project ownership to data + cleanup routes (#54, #55)

**Files:** Modify `src/api/routes.py`. Verify with `tests/test_auth.py`, `tests/test_retention.py`, `tests/test_publish_route_provenance.py`.

**Interfaces — Consumes:** `check_project_access` (Task 1); `require` (existing); `get_tenant_manager` — **reuse the accessor pattern in `tenant_routes.py:95`** (a `get_tenant_manager(request)` returning a `TenantManager`); if it isn't importable/shared, add an equivalent local accessor in `routes.py` following that exact pattern (confirm the `TenantManager` constructor arg against `src/core/tenant.py`). If the accessor shape is ambiguous, STOP and report NEEDS_CONTEXT rather than guessing.

For each route below: change `dependencies=[Depends(require(P))]` → add a parameter `identity: Identity = Depends(require(P))` (import `Identity` from `src.core.identity` at top), resolve `tenant_mgr = get_tenant_manager(request)`, and call the helper as the first line of the body (after existing arg parsing).

| Route (routes.py) | Permission (unchanged) | Ownership call |
|---|---|---|
| POST `/data/publish` | PUBLISH_DATA | `await check_project_access(identity, event.project_id, tenant_mgr, create_if_absent=True)` |
| POST `/data/upload` | PUBLISH_DATA | `create_if_absent=True` on the upload's project_id |
| POST `/projects/{project_id}/import` | PUBLISH_DATA | `create_if_absent=True` |
| POST `/batch/publish` | PUBLISH_DATA | `create_if_absent=True` for each distinct project_id in the batch |
| POST `/projects/{project_id}/query` | QUERY_DATA | `create_if_absent=False` |
| GET `/projects/{project_id}/events` | VIEW_PROJECT_EVENTS | `create_if_absent=False` |
| GET `/projects/{project_id}/data` | VIEW_PROJECT_DATA | `create_if_absent=False` |
| POST `/admin/cleanup/{project_id}` | SYSTEM_CLEANUP | `create_if_absent=False` (#55) |
| GET `/admin/retention/{project_id}` | SYSTEM_CLEANUP | `create_if_absent=False` (#55) |

Leave `POST /admin/cleanup` (all-projects) as-is — it is a global `SYSTEM_CLEANUP` op, not project-scoped; note in the report that per-tenant scoping of the global sweep is a separate consideration.

- [ ] Step 1: add imports + accessor. Step 2: apply the table. Step 3: add tests to `tests/test_ownership.py` or a route test — with multi-tenant ON, a key for tenant A gets 403 on `GET /projects/{p_of_B}/data`, 200 on its own project, and publish to a new project binds it. With multi-tenant OFF, all pass unchanged. Step 4: `pytest tests/test_auth.py tests/test_retention.py tests/test_publish_route_provenance.py -v`. Step 5: commit `feat(authz): enforce project→tenant ownership on data and cleanup routes (#54, #55)`.

---

## Task 3: API-key tenant-scoping (#43)

**Files:** Modify `src/api/routes.py` (the `/auth/keys` routes), `src/core/auth.py` (`revoke_api_key`). Test: `tests/test_auth.py`.

**Interfaces — Consumes:** `identity: Identity = Depends(require(P))`.
**Produces:** `revoke_api_key(db, key_id, tenant_id=None)` — when `tenant_id` is provided, only revokes a key whose `tenant_id` matches; returns False otherwise.

- [ ] **Step 1: failing tests** — in `tests/test_auth.py`: `list_api_keys(db, tenant_id="t1")` returns only t1 keys; `revoke_api_key(db, key_id, tenant_id="t1")` returns False (no delete) when the key belongs to t2, True when it matches.
- [ ] **Step 2: run → fail** (revoke signature).
- [ ] **Step 3: implement**
  - `src/core/auth.py`: add `tenant_id: Optional[str] = None` to `revoke_api_key`; when set, load the key first and only delete if `key.tenant_id == tenant_id`, else return False.
  - `src/api/routes.py`:
    - `GET /auth/keys`: take `identity: Identity = Depends(require(Permission.LIST_API_KEYS))`; call `list_api_keys(db, tenant_id=identity.tenant_id)`.
    - `DELETE /auth/keys/{key_id}`: `identity = Depends(require(REVOKE_API_KEY))`; call `revoke_api_key(db, key_id, tenant_id=identity.tenant_id)`; 404/appropriate if it returns False.
    - `POST /auth/keys`: `identity = Depends(require(CREATE_API_KEY))`; pass `tenant_id=identity.tenant_id` to `create_api_key` so new keys inherit the creator's tenant.
  - Gate: when `identity.tenant_id is None` (single-tenant/tenant-less admin), pass `tenant_id=None` — preserves current cross-tenant behavior for un-tenanted admin keys. (Consistent with the Task 1 gating rationale.)
- [ ] **Step 4: run** `pytest tests/test_auth.py -v`.
- [ ] **Step 5: commit** `fix(authz): scope API key list/revoke/create to the caller's tenant (#43)`.

---

## Task 4: `/sandbox/subscribe` project-scope check

**Files:** Modify `src/web/routes.py`. Test: `tests/test_sandbox_live.py` / `tests/test_sandbox_watch.py`.

`/subscribe` already carries `require(Permission.QUERY_DATA)`. Change it to bind the identity and check ownership (read mode) so a hardened multi-tenant deployment can't stream another tenant's project.

- [ ] Step 1: change `dependencies=[Depends(require(Permission.QUERY_DATA))]` → param `identity: Identity = Depends(require(Permission.QUERY_DATA))`; resolve `tenant_mgr` (same accessor); `await check_project_access(identity, project_id, tenant_mgr, create_if_absent=False)` before starting the stream. Step 2: existing sandbox tests still pass with auth off (helper is a no-op). Step 3: commit `fix(authz): scope sandbox /subscribe to the caller's tenant project`.

---

## Task 5: Remove the dead `require_admin_permission` guard (#39 remnant)

**Files:** `src/api/tenant_routes.py`, `webhook_routes.py`, `service_account_routes.py`, `audit_routes.py`.

The guard reads `request.state.api_key_role` (never set → fail-open) and is now redundant: every one of these routes already carries `require(Permission.MANAGE_*/VIEW_*)`, which is admin-only under the role map. Remove the four `async def require_admin_permission` definitions and every `_: None = Depends(require_admin_permission)` parameter (~28 sites). Change nothing else in the handlers.

- [ ] Step 1: delete the definitions + all `Depends(require_admin_permission)` params. Step 2: `grep -rn "require_admin_permission" src/` → empty. Step 3: `pytest tests/test_tenant.py tests/test_webhooks.py tests/test_audit.py tests/test_security.py -v` (unchanged — require() still gates). Step 4: commit `chore(authz): remove redundant fail-open require_admin_permission guard (#39)`.

---

## Task 6: End-to-end multi-tenant isolation matrix

**Files:** Create `tests/test_tenant_isolation.py`.

With `MULTI_TENANT_ENABLED=true` and `AUTH_ENABLED=true` (monkeypatch `authz.auth_enabled`→True; set the env for `MULTI_TENANT_ENABLED` or monkeypatch `ownership.MULTI_TENANT_ENABLED`), against the real app with `get_identity` overridden to a tenant-A identity:
- `GET /api/v1/projects/{B_project}/data` → 403; `{A_project}` → not 403.
- `GET /api/v1/auth/keys` returns only tenant-A keys (seed keys for A and B).
- Publish to a new project → binds to A (subsequent read by A allowed, by B 403).
- With `MULTI_TENANT_ENABLED=false`: the same cross-tenant calls are NOT 403 (isolation off).

- [ ] Step 1: write the tests (use `AsyncClient`+`ASGITransport`, `app.dependency_overrides[get_identity]`, seed via the `db` fixture; pre-create `TenantProject` rows for A/B). Step 2: run. Step 3: commit `test(authz): multi-tenant project + key isolation matrix`.

---

## Self-Review
- **#43** → Task 3. **#54** → Task 2 (data routes). **#55** → Task 2 (cleanup routes). **/sandbox/subscribe** → Task 4. **#39 remnant** → Task 5. Helper + gating → Task 1. Isolation proof → Task 6.
- **Placeholder scan:** helper + tests are concrete; route tasks use explicit mapping tables + the shared edit pattern. The one soft spot is the `get_tenant_manager` accessor/constructor shape — Task 2 instructs verify-or-NEEDS_CONTEXT rather than guess.
- **Type consistency:** `check_project_access(identity, project_id, tenant_mgr, *, create_if_absent)`, `Identity` fields, `revoke_api_key(db, key_id, tenant_id=None)`, `list_api_keys(db, tenant_id=None)` used consistently across tasks.
- **Gating consistency:** every ownership path is a no-op unless multi-tenant + identity.tenant_id — single-tenant/demo unaffected (verified in Task 1 tests and Task 6's multi-tenant-off case).
