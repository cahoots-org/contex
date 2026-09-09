# Always-On Tenancy + Hardened-Config Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire `MULTI_TENANT_ENABLED` so tenant isolation is enforced exactly when authentication is on, and add a fail-closed hardened-config boot preflight — making the secure posture the only posture.

**Architecture:** Tenant data is already always-populated (#130). Move the *enforcement* gate in `ownership.py`/`subscriptions.py` and the tenant middleware from the standalone `MULTI_TENANT_ENABLED` flag onto `auth_enabled()`. Auth on ⇒ isolation always enforced; auth off ⇒ demo, everyone in `'default'`, nothing enforced. Add `check_hardened_config()` to the boot lifespan that refuses to start when auth is on but `SERVICE_ACCOUNT_JWT_SECRET` is missing.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Postgres+pgvector, pytest (live DB via `tests/conftest.py`; full suite via plain shell — a watchdog kills suite-running subagents, so agents run TARGETED tests only).

**Spec:** `docs/superpowers/specs/2026-09-09-always-on-tenancy-hardened-config-design.md`

## Global Constraints

- **No DB migration.** Tenancy data model is unchanged since #130; alembic head stays 007.
- **Clean break, no back-compat alias.** `MULTI_TENANT_ENABLED` is removed everywhere and read nowhere; do not keep it as a dead/deprecated var.
- **Auth-off-by-default stays.** Do NOT flip the `AUTH_ENABLED` default or add `AUTH_ENABLED=true` to `docker-compose.yml`. Auth-off is the intentional demo posture.
- **Enforcement gate = `auth_enabled()`**, which reads `os.getenv("AUTH_ENABLED")` **at call time**. Tests drive behavior with `monkeypatch.setenv("AUTH_ENABLED", "true"/"false")` — NOT by patching module references (avoids bound-import fragility).
- **Style (Rob):** top-level imports, no meta/"per-task" comments, concise docstrings, don't leak internals in error messages.
- **Tests:** `export DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"`. No new `tests/` failures. Full suite via plain shell only.
- Boot preflight raises `RuntimeError` naming the missing env var, never its value.

## File Structure

- `src/core/ownership.py` — enforcement gate → `auth_enabled()` (Task 1)
- `src/core/subscriptions.py` — `_assert_sub_tenant` gate → `auth_enabled()` (Task 1)
- `src/core/tenant_middleware.py` — drop the flag; auth-off fast-path via `auth_enabled()`; always-mountable (Task 2)
- `main.py` — always add tenant middleware; wire `check_hardened_config()` (Tasks 2, 3)
- `src/core/hardened_config.py` — NEW, boot preflight (Task 3)
- `.env.example`, `README.md` — drop `MULTI_TENANT_ENABLED`; document hardening (Tasks 2, 3)
- Tests reworked: `test_ownership.py`, `test_route_ownership.py`, `test_subscription_ownership.py`, `test_mcp_subscription_tools.py`, `test_tenant_isolation.py` (Task 1); `test_tenant.py` (Task 2); NEW `test_hardened_config.py` (Task 3)

## The MULTI_TENANT_ENABLED reference inventory (all must be gone after Task 2)

Runtime: `tenant_middleware.py:42,103` · `ownership.py:5,18` · `subscriptions.py:13,19` · `main.py:339,361`.
Tests (monkeypatch the flag): `test_ownership.py`, `test_route_ownership.py`, `test_subscription_ownership.py`, `test_mcp_subscription_tools.py`, `test_tenant_isolation.py`, `test_tenant.py`.
Config/docs: `.env.example:139`, `README.md`. (Historical `docs/audits/**` and `docs/superpowers/plans|specs/**` keep the name as a record — do not edit.)

---

## Task 1: Move the enforcement gate from the flag to `auth_enabled()`

**Files:**
- Modify: `src/core/ownership.py` (whole file, currently 30 lines)
- Modify: `src/core/subscriptions.py:11-20`
- Test: `tests/test_ownership.py`, `tests/test_subscription_ownership.py`, `tests/test_route_ownership.py`, `tests/test_mcp_subscription_tools.py`, `tests/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `src.core.authz.auth_enabled() -> bool` (reads `AUTH_ENABLED` env live).
- Produces: `ensure_project_access(identity, project_id, tenant_mgr, *, create_if_absent: bool) -> None` (signature unchanged); `_assert_sub_tenant(row, tenant_id) -> None` (unchanged) — both now no-op iff auth is off.

- [ ] **Step 1: Rewrite the ownership unit tests to drive via `AUTH_ENABLED`.** Replace the whole flag-based setup in `tests/test_ownership.py`. New file body:

```python
import pytest
from unittest.mock import AsyncMock

from src.core.ownership import ensure_project_access
from src.core.identity import Identity
from src.core.rbac import Permission


def _ident(tenant_id, projects=()):
    return Identity(key_id="k", scopes=frozenset({Permission.PUBLISH_DATA}),
                    tenant_id=tenant_id, projects=tuple(projects), role=None)


@pytest.mark.asyncio
async def test_noop_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    mgr = AsyncMock()
    await ensure_project_access(_ident("t1"), "p1", mgr, create_if_absent=False)
    mgr.get_project_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_read_denies_unowned_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1", projects=["p1"]), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_read_denies_other_tenant_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t2"
    with pytest.raises(PermissionError):
        await ensure_project_access(_ident("t1", projects=["p1"]), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_read_allows_owned_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = "t1"
    await ensure_project_access(_ident("t1", projects=["p1"]), "p1", mgr, create_if_absent=False)


@pytest.mark.asyncio
async def test_publish_binds_new_project(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    mgr = AsyncMock(); mgr.get_project_tenant.return_value = None
    await ensure_project_access(_ident("t1", projects=["pnew"]), "pnew", mgr, create_if_absent=True)
    mgr.add_project.assert_awaited_once_with("t1", "pnew")
```

Note: the old `test_noop_when_identity_has_no_tenant` is **removed** — under auth-on, `identity.tenant_id` is never None (NOT NULL column), so the None special-case is deleted. The `has_project` gate (line 20) requires the identity to carry the project, so owned-project tests now pass `projects=["p1"]`.

- [ ] **Step 2: Run — expect failures** (ownership still imports the removed flag / still no-ops on the flag):

Run: `python -m pytest tests/test_ownership.py -v`
Expected: FAIL (behavior still keyed to `MULTI_TENANT_ENABLED`).

- [ ] **Step 3: Rewrite `src/core/ownership.py`** to gate on auth:

```python
"""Project→tenant ownership enforcement for authorized routes."""
from __future__ import annotations

from src.core.authz import auth_enabled
from src.core.identity import Identity


async def ensure_project_access(
    identity: Identity, project_id: str, tenant_mgr, *, create_if_absent: bool
) -> None:
    """Raise PermissionError if the caller's tenant may not access `project_id`.

    No-op when auth is off (demo mode). When `create_if_absent` is True (publish
    paths), an unowned project is bound to the caller's tenant instead of raising.
    """
    if not auth_enabled():
        return
    if not identity.has_project(project_id):
        raise PermissionError("Permission denied")
    owner = await tenant_mgr.get_project_tenant(project_id)
    if owner is None:
        if create_if_absent:
            await tenant_mgr.add_project(identity.tenant_id, project_id)
            return
        raise PermissionError("Permission denied")
    if owner != identity.tenant_id:
        raise PermissionError("Permission denied")
```

- [ ] **Step 4: Update `src/core/subscriptions.py`.** Change the import block (lines 11-13) and `_assert_sub_tenant` (lines 18-20):

```python
from src.core.db_models import Subscription
from src.core.tenant import DEFAULT_TENANT_ID
from src.core.authz import auth_enabled


def _assert_sub_tenant(row, tenant_id):
    if auth_enabled() and tenant_id is not None and row.tenant_id != tenant_id:
        raise PermissionError("Permission denied")
```

(Drop the `from src.core.tenant_middleware import MULTI_TENANT_ENABLED` import. Keep the `tenant_id is not None` guard: callers may pass `None` to mean "don't scope".)

- [ ] **Step 5: Rework the remaining flag-based tests to `setenv`.** In `tests/test_subscription_ownership.py`, `tests/test_mcp_subscription_tools.py`, `tests/test_route_ownership.py`, and `tests/test_tenant_isolation.py`, replace every:
  - `monkeypatch.setattr(ownership, "MULTI_TENANT_ENABLED", True)` and `monkeypatch.setattr("src.core.subscriptions.MULTI_TENANT_ENABLED", True)` → `monkeypatch.setenv("AUTH_ENABLED", "true")`
  - `...MULTI_TENANT_ENABLED", False)` → `monkeypatch.setenv("AUTH_ENABLED", "false")`
  - Remove now-unused `from src.core import ownership` / `import ... subscriptions as subscriptions_module` imports if they were only used for the flag patch.
  - Where a test also does `monkeypatch.setattr(authz, "auth_enabled", lambda: True)` (e.g. `test_tenant_isolation.py:282`), replace it with `monkeypatch.setenv("AUTH_ENABLED", "true")` too, so the real `auth_enabled()` returns True for the ownership call (dependency_overrides still supply the identity). Rename `test_cross_tenant_data_not_403_when_isolation_off` → `test_cross_tenant_data_not_403_when_auth_off` and its docstring to "when auth off".

- [ ] **Step 6: Run the reworked suites — expect PASS:**

Run: `python -m pytest tests/test_ownership.py tests/test_subscription_ownership.py tests/test_mcp_subscription_tools.py tests/test_route_ownership.py tests/test_tenant_isolation.py -v`
Expected: PASS. Also grep: `git grep -n MULTI_TENANT_ENABLED src/core/ownership.py src/core/subscriptions.py` returns nothing.

- [ ] **Step 7: Commit**

```bash
git add src/core/ownership.py src/core/subscriptions.py tests/test_ownership.py tests/test_subscription_ownership.py tests/test_mcp_subscription_tools.py tests/test_route_ownership.py tests/test_tenant_isolation.py
git commit -m "refactor(tenancy): enforce tenant isolation whenever auth is on (drop flag gate from core)"
```

---

## Task 2: Retire `MULTI_TENANT_ENABLED` — always-on middleware, demo forces default

**Files:**
- Modify: `src/core/tenant_middleware.py` (lines 42, 103-107, 179-207)
- Modify: `main.py` (line 339 import, lines 361-364 wiring)
- Modify: `.env.example` (line 139), `README.md`
- Test: `tests/test_tenant.py:585`, and the middleware behavior in `tests/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `src.core.authz.auth_enabled()`, `src.core.tenant.DEFAULT_TENANT_ID`.
- Produces: `TenantMiddleware` / `TenantQuotaMiddleware` (always mounted); demo (auth-off) requests get `request.state.tenant_id = DEFAULT_TENANT_ID` with no DB lookup and no `X-Tenant-ID`/path selection.

- [ ] **Step 1: Failing test for demo default + always-on mount.** Add to `tests/test_tenant.py` (top-level, uses the existing `tm` import alias for `src.core.tenant_middleware`):

```python
@pytest.mark.asyncio
async def test_demo_mode_forces_default_tenant(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    from starlette.requests import Request
    mw = tm.TenantMiddleware(app=None)

    captured = {}
    async def call_next(request):
        captured["tenant_id"] = request.state.tenant_id
        from starlette.responses import Response
        return Response("ok")

    scope = {"type": "http", "method": "GET", "path": "/api/v1/projects/p/data",
             "headers": [(b"x-tenant-id", b"spoofed")]}
    req = Request(scope)
    await mw.dispatch(req, call_next)
    assert captured["tenant_id"] == tm.DEFAULT_TENANT_ID  # X-Tenant-ID ignored in demo
```

- [ ] **Step 2: Run — expect FAIL** (auth-off currently only short-circuits when `MULTI_TENANT_ENABLED` is false, which it is by default, so this may pass for the wrong reason; assert also that the header is ignored). If it errors on the removed symbol later, that's expected pre-implementation.

Run: `python -m pytest tests/test_tenant.py::test_demo_mode_forces_default_tenant -v`

- [ ] **Step 3: Edit `src/core/tenant_middleware.py`.**
  - Delete the constant (line 42): `MULTI_TENANT_ENABLED = os.getenv(...)`.
  - In `dispatch`, change the skip branch (was line 103) from `if not MULTI_TENANT_ENABLED:` to:

```python
        # Demo mode (auth off): single implicit default tenant, no enforcement.
        if not _authz.auth_enabled():
            request.state.tenant_id = DEFAULT_TENANT_ID
            request.state.tenant = None
            return await call_next(request)
```

  - Simplify `_identify_tenant` (this branch is now only reached with auth on) to delegate to the identity path and drop the unauthenticated header/`/t/` selection:

```python
    async def _identify_tenant(self, request: Request, manager: TenantManager) -> Optional[str]:
        """Derive tenant authoritatively from the caller's identity (auth is on here).

        Raises _TenantSpoofingError if X-Tenant-ID disagrees with identity.tenant_id.
        """
        return await self._identify_tenant_from_identity(request)
```

  (Leave `_identify_tenant_from_identity` unchanged.)

- [ ] **Step 4: Edit `main.py`.**
  - Line 339: `from src.core.tenant_middleware import TenantMiddleware, TenantQuotaMiddleware` (drop `, MULTI_TENANT_ENABLED`).
  - Replace the guarded block (lines 361-364):

```python
# Tenant middleware always runs: it sets the default-tenant context in demo mode
# and enforces identity-derived tenant + quotas when auth is on.
app.add_middleware(TenantQuotaMiddleware)
app.add_middleware(TenantMiddleware)
logger.info("Tenant middleware enabled")
```

- [ ] **Step 5: Update `tests/test_tenant.py:585`** — replace `monkeypatch.setattr(tm, "MULTI_TENANT_ENABLED", True)` with `monkeypatch.setenv("AUTH_ENABLED", "true")`. Scan the file for any other `MULTI_TENANT_ENABLED` and convert the same way.

- [ ] **Step 6: Config + docs.**
  - `.env.example`: delete line 139 (`MULTI_TENANT_ENABLED=false ...`).
  - `README.md`: remove any `MULTI_TENANT_ENABLED` mention; where multi-tenancy is described, state it turns on automatically under `AUTH_ENABLED=true` (a single tenant is the default and invisible).

- [ ] **Step 7: Run + grep clean.**

Run: `python -m pytest tests/test_tenant.py tests/test_tenant_isolation.py -v` and `python -c "import main"`
Expected: PASS; import OK. Then: `git grep -n MULTI_TENANT_ENABLED -- ':!docs/audits' ':!docs/superpowers'` returns **nothing**.

- [ ] **Step 8: Commit**

```bash
git add src/core/tenant_middleware.py main.py .env.example README.md tests/test_tenant.py
git commit -m "refactor(tenancy): retire MULTI_TENANT_ENABLED; tenant middleware always on, demo uses default tenant"
```

---

## Task 3: Hardened-config boot preflight (reframed #36)

**Files:**
- Create: `src/core/hardened_config.py`
- Modify: `main.py` (lifespan, near the `check_protected_mode` call ~line 246-251)
- Create: `tests/test_hardened_config.py`
- Modify: `README.md` (hardening section)

**Interfaces:**
- Consumes: `src.core.authz.auth_enabled()`.
- Produces: `check_hardened_config() -> None` — raises `RuntimeError` when auth is on and a no-safe-default secret is missing; warns on soft gaps; no-op when auth off.

- [ ] **Step 1: Failing tests** (`tests/test_hardened_config.py`):

```python
import pytest

from src.core.hardened_config import check_hardened_config


def test_noop_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    check_hardened_config()  # must not raise


def test_raises_when_auth_on_and_jwt_secret_missing(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("SERVICE_ACCOUNT_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT_JWT_SECRET"):
        check_hardened_config()


def test_passes_when_auth_on_and_jwt_secret_set(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "x" * 32)
    check_hardened_config()  # must not raise


def test_warns_on_missing_pepper(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SERVICE_ACCOUNT_JWT_SECRET", "x" * 32)
    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    with caplog.at_level(logging.WARNING):
        check_hardened_config()
    assert any("API_KEY_PEPPER" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run — expect FAIL** (module missing).

Run: `python -m pytest tests/test_hardened_config.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `src/core/hardened_config.py`:**

```python
"""Boot-time hardened-config preflight: fail closed when auth is on but secrets are missing."""
from __future__ import annotations

import os

from src.core.authz import auth_enabled
from src.core.logging import get_logger

logger = get_logger(__name__)


def check_hardened_config() -> None:
    """Refuse to boot when auth is on but a no-safe-default secret is unset.

    Soft gaps (missing pepper, wildcard CORS) are warnings, not failures.
    No-op when auth is off (demo mode).
    """
    if not auth_enabled():
        return

    if not os.getenv("SERVICE_ACCOUNT_JWT_SECRET"):
        raise RuntimeError(
            "SERVICE_ACCOUNT_JWT_SECRET must be set when AUTH_ENABLED=true"
        )

    if not os.getenv("API_KEY_PEPPER"):
        logger.warning(
            "API_KEY_PEPPER not set - API keys hashed with plain SHA-256 "
            "(acceptable for high-entropy keys; set a pepper for defense in depth)"
        )

    cors = os.getenv("CORS_ORIGINS", "*")
    if "*" in cors:
        logger.warning(
            "CORS_ORIGINS allows a wildcard origin; credentials are disabled under wildcard"
        )
```

- [ ] **Step 4: Run — expect PASS.**

Run: `python -m pytest tests/test_hardened_config.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into `main.py` lifespan.** Add the import at top (`from src.core.hardened_config import check_hardened_config`) and call it in `lifespan` immediately after the `check_protected_mode(...)` block (~line 251), before the MCP wiring:

```python
    check_hardened_config()
    logger.info("Hardened-config preflight passed")
```

- [ ] **Step 6: Verify boot is still lazy-safe and demo still boots.**

Run: `python -c "import main"` (must succeed — no import-time evaluation).
Run (demo boots): `AUTH_ENABLED=false python -c "import main"` → OK.
Run (hardened fail-closed): `AUTH_ENABLED=true python -c "import asyncio, main; asyncio.run(main.lifespan(main.app).__aenter__())"` with no `SERVICE_ACCOUNT_JWT_SECRET` → raises RuntimeError naming the var. (If driving the lifespan directly is awkward, assert this via a focused test that calls `check_hardened_config()` — already covered in Step 1.)

- [ ] **Step 7: Document the hardening story in `README.md`.** In the auth/production section, state: set `AUTH_ENABLED=true`; provide `SERVICE_ACCOUNT_JWT_SECRET` (required — server refuses to boot without it) and `API_KEY_PEPPER` (recommended); tenant isolation turns on automatically. Keep it to a short block.

- [ ] **Step 8: Commit**

```bash
git add src/core/hardened_config.py main.py tests/test_hardened_config.py README.md
git commit -m "feat(config): fail-closed hardened-config boot preflight (#36)"
```

---

## Self-Review

- **Spec coverage:**
  - Retire `MULTI_TENANT_ENABLED` → Tasks 1 (core) + 2 (middleware/main/docs); inventory grep in Task 2 Step 7 proves completeness.
  - Enforcement coextensive with `auth_enabled()` → Tasks 1 & 2.
  - Demo forces `'default'`, drops unauthenticated tenant selection → Task 2 Steps 3.
  - Fail-closed preflight (JWT required, pepper/CORS warn) → Task 3.
  - Auth-off default preserved; no compose change → Global Constraints + no task touches the default.
  - No migration → Global Constraints (head 007 untouched).
  - Docs cleanup + hardening story → Task 2 Step 6, Task 3 Step 7.
- **Placeholder scan:** none — every code and test block is concrete.
- **Type consistency:** `ensure_project_access(identity, project_id, tenant_mgr, *, create_if_absent)` and `_assert_sub_tenant(row, tenant_id)` signatures unchanged; `auth_enabled()`/`check_hardened_config()` used consistently; `DEFAULT_TENANT_ID` referenced via `tm.DEFAULT_TENANT_ID` / `src.core.tenant`.
- **Behavioral-change caveat** (release note): deployments on `AUTH_ENABLED=true` + `MULTI_TENANT_ENABLED=false` gain isolation automatically after this change. Surface in the PR body.
- **Full-suite risk:** any other test that sets `AUTH_ENABLED=true` but relied on isolation being off will now enforce. The whole-branch review + final full suite (plain shell) catch stragglers; implementers should `git grep -n 'AUTH_ENABLED' tests/` when a suite fails unexpectedly.
