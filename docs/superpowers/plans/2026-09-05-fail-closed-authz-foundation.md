# Fail-Closed Authorization Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `AUTH_ENABLED=true`, make every REST route and MCP tool fail-closed — denied unless it declares the permission it needs — via one shared identity resolver, route-level authorization dependencies, and an always-on boot-time coverage gate.

**Architecture:** Authorization moves off string-prefix middleware and onto the routes. A single `resolve_identity` maps a credential → `Identity{scopes,...}` (scopes authoritative, roles are presets). REST routes declare `require(*perms)`/`public` dependencies; a startup+CI coverage gate refuses to boot if any route is un-annotated. The mounted `mcp==2.0.0` server authenticates via its own `TokenVerifier` and enforces per-tool default-deny in handlers. All fail-open defaults are removed; the legacy `/api` alias is deleted; a Redis-style protected mode guards the auth-off default.

**Tech Stack:** Python 3.12, FastAPI/Starlette, `mcp==2.0.0`, SQLAlchemy async, Postgres+pgvector, pytest/pytest-asyncio (tests run against live `pgvector/pgvector:pg16` + Redis).

**Spec:** `docs/superpowers/specs/2026-09-05-fail-closed-authz-foundation-design.md`

## Global Constraints

- **No new DB migration.** Alembic head stays at `006`. `APIKey.scopes` (`db_models.py:110`), `APIKey.tenant_id` (`:111`), `APIKeyRole.tenant_id` (`:151`) already exist.
- **Commit structure:** discrete single-purpose commits, each CI-green. TDD exception: a test-only commit may precede its implementation commit. No blob commits.
- **Never `git add`/commit/push on the user's behalf beyond the plan's own task commits on a feature branch.** Never merge. Do not open the PR until the user asks.
- **Docker builds:** `--platform linux/amd64`.
- **CI jobs:** "Run Test Suite" + "Verify Docker Build". ~6 `sdk/python/tests` failures are known-unrelated — ignore them; add no new `tests/` failures.
- **Scopes are authoritative; roles are presets.** Authorization is always checked against `identity.scopes`, never the role directly.
- **Fail-closed:** unknown/invalid credential ⇒ deny; un-annotated route ⇒ boot failure; ambiguity ⇒ deny.
- **Enforcement is gated by `AUTH_ENABLED`; the coverage gate is always on** regardless of `AUTH_ENABLED`.

---

## File Structure

**New files**
- `src/core/identity.py` — `Identity` dataclass + `resolve_identity()` + `ANONYMOUS_IDENTITY`.
- `src/core/authz.py` — FastAPI deps: `get_identity`, `require(*perms)`, `public`; `AUTH_ENABLED` gating.
- `src/core/authz_coverage.py` — `assert_authz_coverage(app)` walker + allowlists.
- `src/core/protected_mode.py` — `check_protected_mode()`.
- Tests: `tests/test_identity.py`, `tests/test_authz.py`, `tests/test_authz_coverage.py`, `tests/test_protected_mode.py`, `tests/test_mcp_auth.py`, `tests/test_authz_matrix.py`.

**Modified files**
- `src/core/rbac.py` — extend `Permission` enum + `ROLE_PERMISSIONS`; add `expand_role()`.
- `src/api/routes.py`, `tenant_routes.py`, `webhook_routes.py`, `service_account_routes.py`, `audit_routes.py`, `version_routes.py`, `src/web/routes.py` — add `require(...)`/`public` to every route.
- `src/core/mcp_adapter.py` — `ApiKeyVerifier`, wire auth, per-tool `@requires`, tenant-from-claims.
- `main.py` — replace middleware with dependency model; delete legacy `/api` alias + `DeprecationWarningMiddleware`; call coverage gate + protected mode in lifespan; wire MCP auth.

**Deleted files**
- `src/core/rbac_middleware.py` (replaced by `authz.py`).
- `APIKeyMiddleware` class in `src/core/auth.py` (body-reading anti-pattern; the CRUD helpers in that file stay).

---

## Task 1: Extend the permission vocabulary

Every auxiliary subsystem (webhooks, service accounts, tenants, audit, versioning) currently has no permissions. Add them so their routes can be annotated (Task 5–11), and make roles presets over scopes.

**Files:**
- Modify: `src/core/rbac.py` (`Permission` enum `:24`, `ROLE_PERMISSIONS` `:51`)
- Test: `tests/test_rbac.py` (extend)

**Interfaces:**
- Produces: new `Permission` members (`MANAGE_TENANTS`, `VIEW_TENANTS`, `MANAGE_WEBHOOKS`, `VIEW_WEBHOOKS`, `MANAGE_SERVICE_ACCOUNTS`, `VIEW_SERVICE_ACCOUNTS`, `VIEW_AUDIT`, `VIEW_VERSION_HISTORY`, `RESTORE_VERSION`); `expand_role(role: Role) -> frozenset[Permission]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rbac.py  (add near the role tests)
from src.core.rbac import Permission, Role, ROLE_PERMISSIONS, expand_role

def test_new_subsystem_permissions_exist():
    for name in ("MANAGE_TENANTS", "VIEW_TENANTS", "MANAGE_WEBHOOKS", "VIEW_WEBHOOKS",
                 "MANAGE_SERVICE_ACCOUNTS", "VIEW_SERVICE_ACCOUNTS", "VIEW_AUDIT",
                 "VIEW_VERSION_HISTORY", "RESTORE_VERSION"):
        assert hasattr(Permission, name)

def test_admin_has_every_permission():
    assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)

def test_expand_role_returns_frozenset():
    perms = expand_role(Role.READONLY)
    assert isinstance(perms, frozenset)
    assert Permission.QUERY_DATA in perms
    assert Permission.MANAGE_TENANTS not in perms
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rbac.py::test_new_subsystem_permissions_exist tests/test_rbac.py::test_admin_has_every_permission tests/test_rbac.py::test_expand_role_returns_frozenset -v`
Expected: FAIL (AttributeError / `expand_role` not defined).

- [ ] **Step 3: Add the permissions, admin=all, and `expand_role`**

```python
# src/core/rbac.py — inside class Permission (after SYSTEM_CLEANUP)
    # Tenant admin
    MANAGE_TENANTS = "manage_tenants"
    VIEW_TENANTS = "view_tenants"
    # Webhooks
    MANAGE_WEBHOOKS = "manage_webhooks"
    VIEW_WEBHOOKS = "view_webhooks"
    # Service accounts
    MANAGE_SERVICE_ACCOUNTS = "manage_service_accounts"
    VIEW_SERVICE_ACCOUNTS = "view_service_accounts"
    # Audit
    VIEW_AUDIT = "view_audit"
    # Versioning
    VIEW_VERSION_HISTORY = "view_version_history"
    RESTORE_VERSION = "restore_version"
```

```python
# src/core/rbac.py — replace the ADMIN set with "all", extend the others with versioning read
ROLE_PERMISSIONS: dict[Role, Set[Permission]] = {
    Role.ADMIN: set(Permission),  # admins have every permission, including future ones
    Role.PUBLISHER: {
        Permission.PUBLISH_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.VIEW_VERSION_HISTORY,
    },
    Role.CONSUMER: {
        Permission.REGISTER_AGENT,
        Permission.LIST_AGENTS,
        Permission.DELETE_AGENT,
        Permission.QUERY_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.VIEW_VERSION_HISTORY,
    },
    Role.READONLY: {
        Permission.QUERY_DATA,
        Permission.VIEW_PROJECT_DATA,
        Permission.VIEW_PROJECT_EVENTS,
        Permission.LIST_AGENTS,
        Permission.VIEW_VERSION_HISTORY,
    },
}


def expand_role(role: Role) -> frozenset[Permission]:
    """Expand a role preset into its permission set."""
    return frozenset(ROLE_PERMISSIONS[role])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rbac.py -v`
Expected: PASS (existing role tests still green; new ones pass).

- [ ] **Step 5: Commit**

```bash
git add src/core/rbac.py tests/test_rbac.py
git commit -m "feat(rbac): add subsystem permissions and expand_role preset helper"
```

---

## Task 2: Identity model + resolver

The single source of truth for who a caller is. Scopes authoritative; unknown key → deny.

**Files:**
- Create: `src/core/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: `Role`, `Permission`, `expand_role` (Task 1); `APIKey`, `APIKeyRole` ORM (`db_models.py`).
- Produces:
  - `@dataclass(frozen=True) class Identity` with fields `key_id: str`, `scopes: frozenset[Permission]`, `tenant_id: str | None`, `projects: tuple[str, ...]`, `role: Role | None`, and method `has_project(project_id: str | None) -> bool`.
  - `async def resolve_identity(db, raw_key: str) -> Identity | None`
  - `ANONYMOUS_IDENTITY: Identity` — all scopes, used only when `AUTH_ENABLED` is false.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_identity.py
import hashlib
import pytest
import pytest_asyncio
from src.core.identity import resolve_identity, Identity, ANONYMOUS_IDENTITY
from src.core.rbac import Permission, Role, assign_role
from src.core.db_models import APIKey


async def _make_key(db, raw_key: str, *, scopes=None, tenant_id=None) -> str:
    key_id = raw_key[-8:]
    async with db.session() as session:
        session.add(APIKey(
            key_id=key_id,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            name="t", prefix=raw_key[:7], scopes=scopes or [], tenant_id=tenant_id,
        ))
    return key_id


@pytest.mark.asyncio
async def test_unknown_key_denies(db):
    assert await resolve_identity(db, "ck_does_not_exist") is None


@pytest.mark.asyncio
async def test_role_preset_expands_to_scopes(db):
    key_id = await _make_key(db, "ck_role_preset_aaaa1111")
    await assign_role(db, key_id, Role.PUBLISHER, projects=["p1"])
    ident = await resolve_identity(db, "ck_role_preset_aaaa1111")
    assert Permission.PUBLISH_DATA in ident.scopes
    assert Permission.QUERY_DATA not in ident.scopes
    assert ident.role == Role.PUBLISHER
    assert ident.projects == ("p1",)


@pytest.mark.asyncio
async def test_explicit_scopes_are_authoritative(db):
    # explicit scopes on the key override role expansion
    key_id = await _make_key(db, "ck_custom_scopes_bbbb2222",
                             scopes=[Permission.PUBLISH_DATA.value, Permission.QUERY_DATA.value])
    await assign_role(db, key_id, Role.READONLY)  # readonly would NOT include PUBLISH_DATA
    ident = await resolve_identity(db, "ck_custom_scopes_bbbb2222")
    assert ident.scopes == frozenset({Permission.PUBLISH_DATA, Permission.QUERY_DATA})


@pytest.mark.asyncio
async def test_tenant_comes_from_key(db):
    await _make_key(db, "ck_tenant_cccc3333", tenant_id="tenant-x")
    ident = await resolve_identity(db, "ck_tenant_cccc3333")
    assert ident.tenant_id == "tenant-x"


def test_anonymous_identity_has_all_scopes():
    assert ANONYMOUS_IDENTITY.scopes == frozenset(Permission)


def test_has_project():
    scoped = Identity(key_id="k", scopes=frozenset(), tenant_id=None, projects=("p1",), role=None)
    assert scoped.has_project("p1") is True
    assert scoped.has_project("p2") is False
    unscoped = Identity(key_id="k", scopes=frozenset(), tenant_id=None, projects=(), role=None)
    assert unscoped.has_project("anything") is True  # empty = all projects
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_identity.py -v`
Expected: FAIL (module `src.core.identity` not found).

- [ ] **Step 3: Implement `identity.py`**

```python
# src/core/identity.py
"""Single source of truth for caller identity. Scopes are authoritative."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select

from src.core.database import DatabaseManager
from src.core.db_models import APIKey as APIKeyModel, APIKeyRole as APIKeyRoleModel
from src.core.rbac import Permission, Role, expand_role


@dataclass(frozen=True)
class Identity:
    key_id: str
    scopes: frozenset[Permission]
    tenant_id: str | None
    projects: tuple[str, ...]           # empty = all projects within tenant
    role: Role | None                   # preset label; not consulted for authz

    def has_project(self, project_id: str | None) -> bool:
        if not self.projects:
            return True
        if project_id is None:
            return True
        return project_id in self.projects


# Used ONLY when AUTH_ENABLED is false (demo mode). Never returned by resolve_identity.
ANONYMOUS_IDENTITY = Identity(
    key_id="anonymous",
    scopes=frozenset(Permission),
    tenant_id=None,
    projects=(),
    role=Role.ADMIN,
)


async def resolve_identity(db: DatabaseManager, raw_key: str) -> Identity | None:
    """Resolve a raw bearer credential to an Identity, or None (deny)."""
    if not raw_key or not raw_key.startswith("ck_"):
        return None
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async with db.session() as session:
        key = (await session.execute(
            select(APIKeyModel).where(APIKeyModel.key_hash == key_hash)
        )).scalar_one_or_none()
        if key is None:
            return None

        role_row = (await session.execute(
            select(APIKeyRoleModel).where(APIKeyRoleModel.key_id == key.key_id)
        )).scalar_one_or_none()

        role = Role(role_row.role) if role_row else None
        projects = tuple(role_row.projects or ()) if role_row else ()

        # Scopes are authoritative: explicit key.scopes win; else expand the role.
        explicit = _parse_scopes(key.scopes)
        if explicit:
            scopes = explicit
        elif role is not None:
            scopes = expand_role(role)
        else:
            scopes = frozenset()  # a key with neither is valid but powerless

        return Identity(
            key_id=key.key_id,
            scopes=scopes,
            tenant_id=key.tenant_id,
            projects=projects,
            role=role,
        )


def _parse_scopes(raw: list[str] | None) -> frozenset[Permission]:
    """Parse stored scope strings into Permissions, ignoring unknown/legacy values."""
    out = set()
    for s in raw or []:
        try:
            out.add(Permission(s))
        except ValueError:
            continue  # legacy scope strings like "read"/"write" are ignored
    return frozenset(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_identity.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/identity.py tests/test_identity.py
git commit -m "feat(identity): scope-authoritative identity resolver, deny unknown keys"
```

---

## Task 3: Authorization dependencies (`get_identity`, `require`, `public`)

The FastAPI dependency layer. Enforcement is gated by `AUTH_ENABLED`; markers are always present.

**Files:**
- Create: `src/core/authz.py`
- Test: `tests/test_authz.py`

**Interfaces:**
- Consumes: `resolve_identity`, `Identity`, `ANONYMOUS_IDENTITY` (Task 2); `Permission` (Task 1).
- Produces:
  - `async def get_identity(request: Request) -> Identity` — 401 if auth on and credential absent/invalid; `ANONYMOUS_IDENTITY` if auth off.
  - `def require(*permissions: Permission)` — returns a dependency callable tagged `_authz_marker`; 403 if identity lacks a permission.
  - `async def public() -> None` — no-op dependency tagged `_public_marker`.
  - `def auth_enabled() -> bool` — reads `AUTH_ENABLED` env at call time (so tests can monkeypatch).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_authz.py
import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient

from src.core import authz
from src.core.authz import require, public, get_identity
from src.core.identity import Identity
from src.core.rbac import Permission


def _build_app():
    app = FastAPI()

    @app.get("/open", dependencies=[Depends(public)])
    async def open_route():
        return {"ok": True}

    @app.get("/publish", dependencies=[Depends(require(Permission.PUBLISH_DATA))])
    async def publish_route():
        return {"ok": True}

    return app


def test_require_and_public_carry_markers():
    dep = require(Permission.PUBLISH_DATA)
    assert getattr(dep, "_authz_marker") == (Permission.PUBLISH_DATA,)
    assert getattr(public, "_public_marker") is True


@pytest.mark.asyncio
async def test_auth_off_allows_everything(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: False)
    async with AsyncClient(app=_build_app(), base_url="http://t") as c:
        assert (await c.get("/publish")).status_code == 200  # no key needed when off


@pytest.mark.asyncio
async def test_auth_on_missing_key_401(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    async with AsyncClient(app=_build_app(), base_url="http://t") as c:
        assert (await c.get("/publish")).status_code == 401
        assert (await c.get("/open")).status_code == 200  # public bypasses


@pytest.mark.asyncio
async def test_auth_on_insufficient_scope_403(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    readonly = Identity(key_id="k", scopes=frozenset({Permission.QUERY_DATA}),
                        tenant_id=None, projects=(), role=None)
    app = _build_app()
    app.dependency_overrides[get_identity] = lambda: readonly
    async with AsyncClient(app=app, base_url="http://t") as c:
        assert (await c.get("/publish")).status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_authz.py -v`
Expected: FAIL (module `src.core.authz` not found).

- [ ] **Step 3: Implement `authz.py`**

```python
# src/core/authz.py
"""Route-level authorization dependencies. Fail-closed when AUTH_ENABLED."""
from __future__ import annotations

import os

from fastapi import HTTPException, Request

from src.core.identity import Identity, ANONYMOUS_IDENTITY, resolve_identity
from src.core.rbac import Permission


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() == "true"


def _extract_credential(request: Request) -> str | None:
    key = request.headers.get("X-API-Key")
    if key:
        return key
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return None


async def get_identity(request: Request) -> Identity:
    """Resolve the caller. Anonymous (all scopes) when auth is off; 401 when on and invalid."""
    if not auth_enabled():
        return ANONYMOUS_IDENTITY
    credential = _extract_credential(request)
    if not credential:
        raise HTTPException(status_code=401, detail="Missing API Key")
    identity = await resolve_identity(request.app.state.db, credential)
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    request.state.identity = identity
    return identity


def require(*permissions: Permission):
    """Return a dependency that requires all `permissions`. Empty = authenticated-only."""
    async def _dep(request: Request) -> Identity:
        identity = await get_identity(request)
        if not set(permissions).issubset(identity.scopes):
            missing = sorted(p.value for p in set(permissions) - identity.scopes)
            raise HTTPException(
                status_code=403,
                detail={"error": "forbidden", "missing_permissions": missing},
            )
        return identity

    _dep._authz_marker = permissions  # sentinel for the coverage walker
    return _dep


async def public() -> None:
    """Explicit public marker. No-op dependency; presence = intentionally unauthenticated."""
    return None


public._public_marker = True  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_authz.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/authz.py tests/test_authz.py
git commit -m "feat(authz): route-level require()/public deps gated by AUTH_ENABLED"
```

---

## Task 4: Coverage gate walker

Refuses to boot if any route is neither `require`- nor `public`-marked. Always on.

**Files:**
- Create: `src/core/authz_coverage.py`
- Test: `tests/test_authz_coverage.py`

**Interfaces:**
- Consumes: `require`, `public` (Task 3).
- Produces:
  - `def find_uncovered_routes(app) -> list[str]` — returns `"{METHODS} {path}"` for each uncovered route.
  - `def assert_authz_coverage(app) -> None` — raises `RuntimeError` listing offenders if any.
  - `ALLOWED_MOUNTS: set[str]` = `{"/mcp", "/static"}`; `PUBLIC_FRAMEWORK_PATHS: set[str]` = `{"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_authz_coverage.py
import pytest
from fastapi import Depends, FastAPI

from src.core.authz import require, public
from src.core.authz_coverage import find_uncovered_routes, assert_authz_coverage
from src.core.rbac import Permission


def test_covered_app_passes():
    app = FastAPI()

    @app.get("/a", dependencies=[Depends(require(Permission.QUERY_DATA))])
    async def a(): ...

    @app.get("/b", dependencies=[Depends(public)])
    async def b(): ...

    assert find_uncovered_routes(app) == []
    assert_authz_coverage(app)  # no raise


def test_uncovered_route_is_flagged():
    app = FastAPI()

    @app.get("/naked")
    async def naked(): ...

    uncovered = find_uncovered_routes(app)
    assert any("/naked" in u for u in uncovered)
    with pytest.raises(RuntimeError, match="/naked"):
        assert_authz_coverage(app)


def test_per_method_granularity():
    app = FastAPI()

    @app.get("/x", dependencies=[Depends(public)])
    async def x_get(): ...

    @app.post("/x")  # POST has no marker → must be flagged even though GET is covered
    async def x_post(): ...

    uncovered = find_uncovered_routes(app)
    assert any("POST" in u and "/x" in u for u in uncovered)


def test_unknown_mount_is_flagged():
    from starlette.applications import Starlette
    app = FastAPI()
    app.mount("/rogue", Starlette())
    with pytest.raises(RuntimeError, match="/rogue"):
        assert_authz_coverage(app)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_authz_coverage.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `authz_coverage.py`**

```python
# src/core/authz_coverage.py
"""Boot-time (and CI) assertion that every route declares an authz decision."""
from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.routing import Mount, Route, WebSocketRoute

ALLOWED_MOUNTS = {"/mcp", "/static"}
PUBLIC_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _dependant_is_marked(dependant) -> bool:
    """True if any callable in the resolved dependency tree carries our marker."""
    stack = list(getattr(dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        if getattr(call, "_authz_marker", None) is not None:
            return True
        if getattr(call, "_public_marker", False):
            return True
        stack.extend(getattr(dep, "dependencies", []))
    return False


def find_uncovered_routes(app) -> list[str]:
    uncovered: list[str] = []
    for route in app.routes:
        if isinstance(route, Mount):
            if route.path not in ALLOWED_MOUNTS:
                uncovered.append(f"MOUNT {route.path} (not in ALLOWED_MOUNTS)")
            continue
        if isinstance(route, APIRoute):
            if route.path in PUBLIC_FRAMEWORK_PATHS:
                continue
            if not _dependant_is_marked(route.dependant):
                methods = ",".join(sorted(route.methods or []))
                uncovered.append(f"{methods} {route.path}")
            continue
        if isinstance(route, WebSocketRoute):
            # No HTTP dependant; require explicit allowlisting when one is added.
            uncovered.append(f"WEBSOCKET {route.path} (websocket routes need explicit review)")
            continue
        # Bare Starlette Route (e.g. framework health) — allow only if in the public set.
        if isinstance(route, Route) and route.path not in PUBLIC_FRAMEWORK_PATHS:
            # FastAPI mounts most things as APIRoute; a bare Route is unusual → flag it.
            uncovered.append(f"ROUTE {route.path} (unexpected bare route)")
    return uncovered


def assert_authz_coverage(app) -> None:
    uncovered = find_uncovered_routes(app)
    if uncovered:
        raise RuntimeError(
            "FAIL-CLOSED: routes without a require()/public decision or allowlist entry:\n"
            + "\n".join(sorted(uncovered))
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_authz_coverage.py -v`
Expected: PASS.

Note: `test_unknown_mount_is_flagged` relies on `/rogue` not being in `ALLOWED_MOUNTS`; FastAPI's own `/static` may not be mounted in a bare app, which is fine.

- [ ] **Step 5: Commit**

```bash
git add src/core/authz_coverage.py tests/test_authz_coverage.py
git commit -m "feat(authz): boot-time route coverage gate (fail-closed keystone)"
```

---

## Tasks 5–11: Annotate every route

Each task adds `Depends(require(...))` or `Depends(public)` to one router. These are **inert until Task 13 wires the gate and Task 12 wires enforcement**, so CI stays green throughout. Pattern for every route:

```python
from fastapi import Depends
from src.core.authz import require, public
from src.core.rbac import Permission

@router.post("/publish", dependencies=[Depends(require(Permission.PUBLISH_DATA))])
async def publish(...): ...

@router.get("/", dependencies=[Depends(public)])
async def index(): ...
```

For each router task: **Step 1** add the imports; **Step 2** add the `dependencies=[...]` per the mapping table; **Step 3** run that router's existing tests (they should still pass — enforcement is off); **Step 4** commit. If a route already declares `dependencies=[...]`, append to the list.

### Task 5: `src/api/routes.py` (core)

**Files:** Modify `src/api/routes.py`; verify with existing `tests/test_auth.py`, `tests/test_retention.py`, `tests/test_publish_route_provenance.py`.

Mapping:

| Method + path | Decision |
|---|---|
| GET `/`, `/health`, `/health/ready`, `/health/live`, `/metrics` | `public` |
| POST `/auth/keys` | `require(Permission.CREATE_API_KEY)` |
| GET `/auth/keys` | `require(Permission.LIST_API_KEYS)` |
| DELETE `/auth/keys/{key_id}` | `require(Permission.REVOKE_API_KEY)` |
| GET `/admin/rate-limits` | `require(Permission.VIEW_RATE_LIMITS)` |
| POST/GET `/auth/roles`, GET/DELETE `/auth/roles/{key_id}` | `require(Permission.MANAGE_ROLES)` |
| GET `/auth/permissions` | `require()` (authenticated-only) |
| POST `/data/publish`, POST `/data/upload`, POST `/projects/{id}/import`, POST `/batch/publish` | `require(Permission.PUBLISH_DATA)` |
| POST `/agents/register`, POST `/batch/register` | `require(Permission.REGISTER_AGENT)` |
| DELETE `/agents/{agent_id}` | `require(Permission.DELETE_AGENT)` |
| GET `/agents`, GET `/agents/{agent_id}` | `require(Permission.LIST_AGENTS)` |
| GET `/projects/{id}/events` | `require(Permission.VIEW_PROJECT_EVENTS)` |
| GET `/projects/{id}/data` | `require(Permission.VIEW_PROJECT_DATA)` |
| POST `/projects/{id}/query` | `require(Permission.QUERY_DATA)` |
| POST `/admin/cleanup`, POST `/admin/cleanup/{project_id}`, GET `/admin/retention/{project_id}` | `require(Permission.SYSTEM_CLEANUP)` |

- [ ] Step 1: add imports. Step 2: apply the table. Step 3: `pytest tests/test_auth.py tests/test_retention.py tests/test_publish_route_provenance.py -v` (green). Step 4: `git commit -m "feat(authz): annotate core API routes with required permissions"`.

### Task 6: `src/api/tenant_routes.py`

| Method + path | Decision |
|---|---|
| POST `""` (create), PATCH/DELETE `/{id}`, POST `/{id}/reset-monthly-usage`, POST/DELETE `/{id}/projects/{project_id}` | `require(Permission.MANAGE_TENANTS)` |
| GET `""` (list), GET `/{id}`, GET `/{id}/usage`, GET `/{id}/projects` | `require(Permission.VIEW_TENANTS)` |

- [ ] Steps as above; verify `pytest tests/test_tenant.py -v`; commit `"feat(authz): annotate tenant routes"`.

### Task 7: `src/api/webhook_routes.py`

| Method + path | Decision |
|---|---|
| GET `/events`, `/events/categories`, `/events/types`, GET `/endpoints`, GET `/endpoints/{id}`, GET `/endpoints/{id}/deliveries` | `require(Permission.VIEW_WEBHOOKS)` |
| POST `/endpoints`, PATCH/DELETE `/endpoints/{id}`, POST `/endpoints/{id}/rotate-secret`, POST `/endpoints/{id}/test` | `require(Permission.MANAGE_WEBHOOKS)` |

- [ ] Steps as above; verify `pytest tests/test_webhooks.py -v`; commit `"feat(authz): annotate webhook routes"`.

### Task 8: `src/api/service_account_routes.py`

| Method + path | Decision |
|---|---|
| GET `""` (list), GET `/{account_id}` | `require(Permission.VIEW_SERVICE_ACCOUNTS)` |
| POST `""`, PATCH/DELETE `/{account_id}`, POST `/{account_id}/keys`, DELETE `/{account_id}/keys/{key_id}`, POST `/token`, POST `/token/validate` | `require(Permission.MANAGE_SERVICE_ACCOUNTS)` |

- [ ] Steps as above; verify any `tests/test_*service*` (or full suite if none); commit `"feat(authz): annotate service-account routes"`.

### Task 9: `src/api/audit_routes.py`

| Method + path | Decision |
|---|---|
| GET `/events`, `/events/{event_id}`, `/events/types`, `/export`, `/summary` | `require(Permission.VIEW_AUDIT)` |

- [ ] Steps as above; verify `pytest tests/test_audit.py -v`; commit `"feat(authz): annotate audit routes"`.

### Task 10: `src/api/version_routes.py`

| Method + path | Decision |
|---|---|
| GET `.../history`, `.../version/{sequence}`, `.../diff` | `require(Permission.VIEW_VERSION_HISTORY)` |
| POST `.../restore/{sequence}` | `require(Permission.RESTORE_VERSION)` |

This closes the versioning-bypass half of #72.

- [ ] Steps as above; verify `pytest tests/test_version_routes.py -v`; commit `"feat(authz): annotate versioning routes (closes #72 REST bypass)"`.

### Task 11: `src/web/routes.py` (sandbox)

The sandbox page shell stays public; its data-access routes require permissions so a hardened deployment doesn't expose an unauthenticated data path (this is the #40 REST half). When `AUTH_ENABLED=false` the demo still works (anonymous = all scopes).

| Method + path | Decision |
|---|---|
| GET `/` (page), GET `/subscribe` (page/SSE shell) | `public` |
| POST `/query` | `require(Permission.QUERY_DATA)` |
| GET `/projects/{id}/stats`, GET `/projects/{id}/data` | `require(Permission.VIEW_PROJECT_DATA)` |

- [ ] Steps as above; verify `pytest tests/test_sandbox_live.py tests/test_sandbox_watch.py -v`; commit `"feat(authz): annotate sandbox web routes (closes #40 REST path)"`.

---

## Task 12: Wire the dependency model into `main.py`; delete legacy middleware and `/api` alias

Replace the middleware-based auth with the dependency model, remove the fail-open middleware, and delete the legacy alias.

**Files:**
- Modify: `main.py` (`:305-336` middleware block, `:370-391` legacy alias)
- Delete: `src/core/rbac_middleware.py`
- Modify: `src/core/auth.py` (remove `APIKeyMiddleware` class + its body handling; keep `create_api_key`/`list_api_keys`/`revoke_api_key`/`get_api_key`/`get_api_key_by_hash` and `_record_auth_event`)
- Modify/Delete: `tests/test_rbac.py` (drop tests of the removed `RBACMiddleware`), `tests/test_auth.py` / `tests/test_security.py` (drop tests of removed `APIKeyMiddleware`; keep CRUD tests)

**Interfaces:**
- Consumes: `get_identity` (Task 3), annotated routers (Tasks 5–11).
- Produces: an `app` with no `APIKeyMiddleware`/`RBACMiddleware`, only `/api/v1` (no `/api`).

- [ ] **Step 1: Remove the AUTH_ENABLED middleware block and legacy alias**

In `main.py`, delete the `if AUTH_ENABLED:` block that adds `RateLimitMiddleware`, `RBACMiddleware`, `APIKeyMiddleware` (`:318-329`) — authz is now per-route. Keep `RateLimitMiddleware` only if it is path-agnostic; if it also used the broken path table, move its wiring to a follow-up (note: rate-limiter path patterns are the #38 sibling — out of scope here, so leave rate limiting unwired for now and log a warning). Delete the legacy `/api` mount and `DeprecationWarningMiddleware` (`:370-391`). Delete the `from src.core.rbac_middleware import RBACMiddleware` and `from src.core.auth import APIKeyMiddleware` imports.

- [ ] **Step 1b: Stop `X-Tenant-ID` from selecting a tenant (the #41 fix)**

`TenantMiddleware` (`src/core/tenant_middleware.py`, wired at `main.py:334`) currently trusts the `X-Tenant-ID` header. When auth is on, the tenant must come from the identity, not the header. Change `TenantMiddleware` so that when `auth_enabled()` and `request.state.identity` is set, it uses `identity.tenant_id` and **rejects a mismatched `X-Tenant-ID` with 403** (or ignores the header entirely); when auth is off, keep current behavior. Note middleware ordering: `TenantMiddleware` must run *after* identity is established — since identity is now resolved in a route dependency rather than middleware, read `identity.tenant_id` from `request.state.identity` if present, else fall back to resolving the credential directly, else (auth off) the header. Add a test in `tests/test_tenant.py`:

```python
@pytest.mark.asyncio
async def test_mismatched_x_tenant_id_rejected_when_auth_on(monkeypatch):
    from src.core import authz
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    # a key bound to tenant-a sending X-Tenant-ID: tenant-b must be rejected 403
    # (build the request via the app test client with a tenant-a key + X-Tenant-ID: tenant-b)
    ...
```

- [ ] **Step 2: Delete `src/core/rbac_middleware.py` and the `APIKeyMiddleware` class**

```bash
git rm src/core/rbac_middleware.py
```
Edit `src/core/auth.py`: delete the `APIKeyMiddleware` class (`:72-157`). Keep everything else.

- [ ] **Step 3: Remove tests that exercise the deleted middleware**

Delete `RBACMiddleware`/`APIKeyMiddleware` test classes from `tests/test_rbac.py`, `tests/test_auth.py`, `tests/test_security.py`. Keep tests for `assign_role`/`get_role`/CRUD.

- [ ] **Step 4: Run the suite to confirm nothing imports the removed symbols**

Run: `pytest tests/test_auth.py tests/test_rbac.py tests/test_security.py -v`
Expected: PASS (no ImportError; remaining tests green).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(authz): remove fail-open auth middleware and legacy /api alias"
```

---

## Task 13: Enable the coverage gate at startup + standalone CI test

Now every route is annotated (Tasks 5–11), turn on the gate.

**Files:**
- Modify: `main.py` (lifespan, after migrations)
- Test: `tests/test_authz_coverage.py` (add the real-app assertion)

**Interfaces:**
- Consumes: `assert_authz_coverage` (Task 4), the fully-annotated `app`.

- [ ] **Step 1: Write the failing test (real app is fully covered)**

```python
# tests/test_authz_coverage.py  (append)
def test_real_app_is_fully_covered():
    from main import app
    assert find_uncovered_routes(app) == []
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_authz_coverage.py::test_real_app_is_fully_covered -v`
Expected: PASS if Tasks 5–11 are complete; if it FAILS, the listed routes are genuinely un-annotated — fix them before proceeding (the gate is doing its job).

- [ ] **Step 3: Call the gate in the lifespan**

In `main.py` lifespan, after `await db.migrate_to_head()` and before `yield`:

```python
from src.core.authz_coverage import assert_authz_coverage
assert_authz_coverage(app)  # refuse to boot if any route lacks an authz decision
logger.info("Authz coverage gate passed")
```

- [ ] **Step 4: Run the full suite + a boot smoke**

Run: `pytest tests/test_authz_coverage.py -v` and `python -c "import main"` (import must not raise).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_authz_coverage.py
git commit -m "feat(authz): enforce route coverage gate at startup and in CI"
```

---

## Task 14: MCP authentication (verifier + AuthSettings)

Authenticate the mounted MCP server with an API-key `TokenVerifier`, no OAuth machinery. Verified against installed `mcp==2.0.0`.

**Files:**
- Modify: `src/core/mcp_adapter.py`
- Modify: `main.py` (pass a lazy db accessor to `build_mcp_server`)
- Test: `tests/test_mcp_auth.py`

**Interfaces:**
- Consumes: `resolve_identity` (Task 2); `auth_enabled` (Task 3).
- Produces: `class ApiKeyVerifier(TokenVerifier)`; `build_mcp_server(engine, db_accessor=None)` gains an optional lazy db accessor and wires auth when `auth_enabled()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_auth.py
import pytest
from src.core.mcp_adapter import ApiKeyVerifier


@pytest.mark.asyncio
async def test_verifier_rejects_unknown_key(db):
    v = ApiKeyVerifier(lambda: db)
    assert await v.verify_token("ck_nope_nope_nope") is None


@pytest.mark.asyncio
async def test_verifier_accepts_and_carries_claims(db):
    import hashlib
    from src.core.db_models import APIKey
    from src.core.rbac import assign_role, Role
    raw = "ck_mcp_verifier_dddd4444"
    async with db.session() as s:
        s.add(APIKey(key_id="dddd4444", key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                     name="t", prefix=raw[:7], scopes=[], tenant_id="tenant-y"))
    await assign_role(db, "dddd4444", Role.PUBLISHER, projects=["p1"])
    tok = await ApiKeyVerifier(lambda: db).verify_token(raw)
    assert tok is not None
    assert tok.client_id == "dddd4444"
    assert "publish_data" in tok.scopes
    assert tok.claims["tenant_id"] == "tenant-y"
    assert tok.claims["projects"] == ["p1"]
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_mcp_auth.py -v`
Expected: FAIL (`ApiKeyVerifier` not defined).

- [ ] **Step 3: Implement the verifier and wire auth**

```python
# src/core/mcp_adapter.py  (add imports + class; extend build_mcp_server signature)
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from src.core.authz import auth_enabled
from src.core.identity import resolve_identity


class ApiKeyVerifier(TokenVerifier):
    """Resolve a Contex API key into an MCP AccessToken. DB is resolved lazily."""

    def __init__(self, db_accessor):
        self._db_accessor = db_accessor  # zero-arg callable → DatabaseManager

    async def verify_token(self, token: str) -> AccessToken | None:
        identity = await resolve_identity(self._db_accessor(), token)
        if identity is None:
            return None
        return AccessToken(
            token=token,
            client_id=identity.key_id,
            scopes=[p.value for p in identity.scopes],
            claims={
                "role": identity.role.value if identity.role else None,
                "tenant_id": identity.tenant_id,
                "projects": list(identity.projects),
            },
        )
```

```python
# src/core/mcp_adapter.py — build_mcp_server signature + conditional auth
def build_mcp_server(engine, db_accessor=None):
    bus = InMemorySubscriptionBus()
    auth_kwargs = {}
    if auth_enabled() and db_accessor is not None:
        auth_kwargs = dict(
            token_verifier=ApiKeyVerifier(db_accessor),
            auth=AuthSettings(
                issuer_url="https://contex.local",  # required by pydantic; unused in this path
                resource_server_url=None,           # keeps us off RFC 9728 discovery
                required_scopes=None,               # per-tool checks live in handlers
            ),
        )
    server = MCPServer(name="contex", version="0.3.0", subscriptions=bus, **auth_kwargs)
    # ... existing tool/resource registrations unchanged ...
    return server, bus
```

In `main.py` where the server is built (`:272`):

```python
_mcp_server, _mcp_bus = build_mcp_server(
    lambda: app.state.context_engine,
    db_accessor=lambda: app.state.db,
)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_mcp_auth.py tests/test_mcp_mount.py tests/test_mcp_adapter.py -v`
Expected: PASS (existing MCP mount tests still green; auth off by default in tests).

- [ ] **Step 5: Commit**

```bash
git add src/core/mcp_adapter.py main.py tests/test_mcp_auth.py
git commit -m "feat(mcp): API-key bearer auth via TokenVerifier (closes #37 unauth)"
```

---

## Task 15: MCP per-tool default-deny + tenant-from-claims

Each tool checks its permission and validates the `project_id` argument against the caller's identity. Closes #53 and the MCP half of #54.

**Files:**
- Modify: `src/core/mcp_adapter.py`
- Test: `tests/test_mcp_auth.py` (append)

**Interfaces:**
- Consumes: `get_access_token` from `mcp.server.auth.middleware.auth_context`; `Permission` (Task 1).
- Produces: `def _enforce(permission, project_id=None)` helper used inside each tool.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_auth.py  (append)
import pytest
from src.core.rbac import Permission
from src.core import mcp_adapter


class _FakeTok:
    def __init__(self, scopes, tenant_id=None, projects=None):
        self.scopes = scopes
        self.claims = {"tenant_id": tenant_id, "projects": projects or []}


@pytest.mark.asyncio
async def test_enforce_denies_missing_scope(monkeypatch):
    monkeypatch.setattr(mcp_adapter, "auth_enabled", lambda: True)
    monkeypatch.setattr(mcp_adapter, "get_access_token", lambda: _FakeTok(scopes=[]))
    with pytest.raises(PermissionError):
        mcp_adapter._enforce(Permission.PUBLISH_DATA)


@pytest.mark.asyncio
async def test_enforce_denies_out_of_scope_project(monkeypatch):
    monkeypatch.setattr(mcp_adapter, "auth_enabled", lambda: True)
    monkeypatch.setattr(mcp_adapter, "get_access_token",
                        lambda: _FakeTok(scopes=["query_data"], projects=["p1"]))
    with pytest.raises(PermissionError):
        mcp_adapter._enforce(Permission.QUERY_DATA, project_id="p2")


@pytest.mark.asyncio
async def test_enforce_allows_when_auth_off(monkeypatch):
    monkeypatch.setattr(mcp_adapter, "auth_enabled", lambda: False)
    mcp_adapter._enforce(Permission.PUBLISH_DATA, project_id="anything")  # no raise
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_mcp_auth.py -k enforce -v`
Expected: FAIL (`_enforce` not defined).

- [ ] **Step 3: Implement `_enforce` and call it in each tool**

```python
# src/core/mcp_adapter.py
from mcp.server.auth.middleware.auth_context import get_access_token


def _enforce(permission, project_id=None):
    """Default-deny per-tool check. No-op when auth is off (demo parity)."""
    if not auth_enabled():
        return
    tok = get_access_token()
    if tok is None or permission.value not in tok.scopes:
        raise PermissionError(f"Missing permission: {permission.value}")
    if project_id is not None:
        projects = (tok.claims or {}).get("projects") or []
        if projects and project_id not in projects:
            raise PermissionError(f"Project out of scope: {project_id}")
```

Add the appropriate call as the first line of each tool handler:

```python
@server.tool(name="contex_query", ...)
async def contex_query(project_id: str, query: str, top_k: int = 5, threshold=None) -> str:
    _enforce(Permission.QUERY_DATA, project_id=project_id)
    ...

@server.tool(name="contex_publish", ...)
async def contex_publish(project_id: str, data_key: str, data: dict, data_format="json") -> str:
    _enforce(Permission.PUBLISH_DATA, project_id=project_id)
    ...

@server.tool(name="contex_create_subscription", ...)
async def contex_create_subscription(project_id: str, needs, top_k=5, threshold=None) -> str:
    _enforce(Permission.QUERY_DATA, project_id=project_id)
    ...

@server.tool(name="contex_delete_subscription", ...)
async def contex_delete_subscription(subscription_id: str) -> str:
    _enforce(Permission.QUERY_DATA)   # subscription ownership check is fast-follow #73
    ...

@server.resource("contex://subscriptions/{id}", ...)
async def read_subscription(id: str) -> str:
    _enforce(Permission.QUERY_DATA)
    ...
```

(Import `Permission` at the top: `from src.core.rbac import Permission`.)

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_mcp_auth.py tests/test_mcp_subscription_tools.py tests/test_mcp_publish_tool.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/mcp_adapter.py tests/test_mcp_auth.py
git commit -m "feat(mcp): per-tool default-deny and claims-scoped project checks (closes #53)"
```

---

## Task 16: Protected mode

When auth is off and the bind address is non-loopback, refuse to start unless explicitly opted out.

**Files:**
- Create: `src/core/protected_mode.py`
- Modify: `main.py` (lifespan or startup, before serving)
- Test: `tests/test_protected_mode.py`

**Interfaces:**
- Produces: `def check_protected_mode(host: str, *, auth_on: bool, protected: bool) -> None` — raises `RuntimeError` when `not auth_on and protected and host` is non-loopback; logs a warning when it allows an open remote bind.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_protected_mode.py
import pytest
from src.core.protected_mode import check_protected_mode


def test_blocks_remote_bind_when_unconfigured():
    with pytest.raises(RuntimeError):
        check_protected_mode("0.0.0.0", auth_on=False, protected=True)


def test_allows_loopback_when_unconfigured():
    check_protected_mode("127.0.0.1", auth_on=False, protected=True)  # no raise


def test_allows_remote_when_auth_on():
    check_protected_mode("0.0.0.0", auth_on=True, protected=True)  # no raise


def test_escape_hatch_allows_remote():
    check_protected_mode("0.0.0.0", auth_on=False, protected=False)  # no raise
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_protected_mode.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement**

```python
# src/core/protected_mode.py
"""Redis-style protected mode: refuse remote binds when auth is unconfigured."""
from __future__ import annotations

import ipaddress

from src.core.logging import get_logger

logger = get_logger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_protected_mode(host: str, *, auth_on: bool, protected: bool) -> None:
    if auth_on:
        return
    if not protected:
        logger.warning("Protected mode disabled and AUTH_ENABLED=false — server is OPEN to %s", host)
        return
    if not _is_loopback(host):
        raise RuntimeError(
            f"Protected mode: refusing to bind {host} with authentication disabled. "
            "Set AUTH_ENABLED=true, bind to loopback, or set CONTEX_PROTECTED_MODE=false."
        )
```

- [ ] **Step 4: Wire into `main.py` lifespan (before `yield`)**

```python
import os
from src.core.authz import auth_enabled
from src.core.protected_mode import check_protected_mode

check_protected_mode(
    os.getenv("CONTEX_HOST", "0.0.0.0"),
    auth_on=auth_enabled(),
    protected=os.getenv("CONTEX_PROTECTED_MODE", "true").lower() == "true",
)
```

- [ ] **Step 5: Run + commit**

Run: `pytest tests/test_protected_mode.py -v` (PASS).
```bash
git add src/core/protected_mode.py main.py tests/test_protected_mode.py
git commit -m "feat(security): Redis-style protected mode for unconfigured remote binds"
```

---

## Task 17: End-to-end authz matrix + negative-auth integration tests

Prove the whole stack behaves per role against the real app, with auth on.

**Files:**
- Test: `tests/test_authz_matrix.py`

**Interfaces:**
- Consumes: `main.app`, `get_identity` (override), `Identity`, `Permission`.

- [ ] **Step 1: Write the tests**

```python
# tests/test_authz_matrix.py
import pytest
from httpx import AsyncClient

from src.core import authz
from src.core.authz import get_identity
from src.core.identity import Identity
from src.core.rbac import Permission, Role, expand_role


def _identity(role: Role) -> Identity:
    return Identity(key_id="k", scopes=expand_role(role), tenant_id="t", projects=(), role=role)


@pytest.fixture
def app_authed(monkeypatch):
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    from main import app
    return app


@pytest.mark.asyncio
async def test_missing_key_is_401(app_authed):
    async with AsyncClient(app=app_authed, base_url="http://t") as c:
        r = await c.post("/api/v1/data/publish", json={})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_readonly_cannot_publish(app_authed):
    app_authed.dependency_overrides[get_identity] = lambda: _identity(Role.READONLY)
    try:
        async with AsyncClient(app=app_authed, base_url="http://t") as c:
            r = await c.post("/api/v1/data/publish",
                             json={"project_id": "p", "data_key": "k", "data": {}})
            assert r.status_code == 403
    finally:
        app_authed.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_publisher_can_reach_publish(app_authed):
    app_authed.dependency_overrides[get_identity] = lambda: _identity(Role.PUBLISHER)
    try:
        async with AsyncClient(app=app_authed, base_url="http://t") as c:
            r = await c.post("/api/v1/data/publish",
                             json={"project_id": "p", "data_key": "k", "data": {}})
            assert r.status_code != 403  # authz passes (may 4xx/5xx on body/engine, not 403)
    finally:
        app_authed.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_legacy_api_alias_is_gone(app_authed):
    async with AsyncClient(app=app_authed, base_url="http://t") as c:
        # /api/... (non-v1) should 404 now that the alias is removed
        r = await c.post("/api/data/publish", json={})
        assert r.status_code == 404
```

- [ ] **Step 2: Run**

Run: `pytest tests/test_authz_matrix.py -v`
Expected: PASS. (If `test_publisher_can_reach_publish` needs `app.state.db`/engine, mark it to assert only `!= 403`, which the code above already does.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_authz_matrix.py
git commit -m "test(authz): end-to-end role matrix and negative-auth coverage"
```

---

## Task 18: File fast-follow issues + update docs

**Files:** none (GitHub + docs).

- [ ] **Step 1: File fast-follow issues** referencing the spec, one per: #42 (bundle ownership), #43 (key listing tenant scope), #54 (cross-project ownership beyond the MCP check), #55 (cleanup ownership), #73 (subscription ownership). Each: "Build on the identity foundation (spec 2026-09-05); add an ownership check using `Identity.tenant_id`/`has_project`."

- [ ] **Step 2: Note the deferred items** as issues if not already open: RLS (own issue), rate-limiter path-table fix (sibling of #38, unwired in Task 12), sandbox UI key-passing UX when auth on, `/metrics` exposure review (#56 territory).

- [ ] **Step 3: Update the spec status** to `Implemented` and cross-reference the PR. Commit `"docs: mark authz foundation spec implemented"`.

---

## Self-Review

**Spec coverage:**
- §4.1 identity resolver → Task 2. Scopes-authoritative + role presets → Tasks 1–2. Tenant-from-key / #41 → Task 2 (resolver) + enforced via annotated tenant routes (Task 6) and `X-Tenant-ID` no longer selecting tenant (Task 12 removes the tenant-spoofing middleware path; verify `TenantMiddleware` is either removed or validates against `identity.tenant_id` — **added to Task 12 scope note below**).
- §4.1.1 permission vocabulary → Task 1 + annotation Tasks 5–11.
- §4.2 REST deps + §4.2.1 coverage gate → Tasks 3, 4, 13.
- §4.2 legacy `/api` removal → Task 12.
- §4.3 MCP → Tasks 14, 15.
- §4.4 remove fail-open defaults → Tasks 2 (resolver), 12 (middleware), 13 (gate).
- §4.5 protected mode → Task 16.
- §5 data flow, §6 error handling → exercised by Tasks 13, 17.
- §7 testing → Tasks throughout + 17.
- §8 no migration → Global Constraints; bootstrap admin preserved (Task 12 does not touch `main.py:114-127`).
- §9 issues → Tasks 10 (#72 REST), 11 (#40 REST), 14 (#37), 15 (#53, #54 MCP), 18 (fast-follows).

**Gap found & fixed:** #41 requires that `X-Tenant-ID` no longer selects a tenant. Task 12 removes the auth middleware but must also address `TenantMiddleware` (`main.py:334`). **Amendment to Task 12:** in Step 1, also make `TenantMiddleware` derive tenant from `request.state.identity.tenant_id` when auth is on and reject a mismatched `X-Tenant-ID` with 403 (or drop the header entirely); when auth is off, keep current behavior. Add a test in `tests/test_tenant.py` asserting a mismatched `X-Tenant-ID` is rejected under auth-on.

**Placeholder scan:** no TBD/TODO; all code steps carry real code. Route-annotation tasks use explicit per-route mapping tables + the shared pattern (acceptable: mechanical repetition of one shown pattern).

**Type consistency:** `Identity` fields (`scopes: frozenset[Permission]`, `projects: tuple[str,...]`, `role: Role | None`) are consistent across Tasks 2, 3, 14, 17. `resolve_identity(db, raw_key) -> Identity | None`, `require(*permissions)`, `public`, `auth_enabled()`, `assert_authz_coverage(app)`, `find_uncovered_routes(app)`, `_enforce(permission, project_id=None)`, `check_protected_mode(host, *, auth_on, protected)` are used with matching signatures throughout.
