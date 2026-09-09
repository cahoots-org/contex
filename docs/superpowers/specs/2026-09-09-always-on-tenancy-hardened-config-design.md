# Always-On Tenancy + Hardened-Config Preflight — Design

**Status:** draft for review
**Date:** 2026-09-09
**Issues:** #36 (auth/RBAC off by default) — reframed; retires `MULTI_TENANT_ENABLED`
**Depends on:** #130 (always-a-tenant foundation), #131 (ownership enforcement), #132 (crypto/config), all merged.

## Goal

Make the secure posture the *only* posture, so a Contex deployment cannot be
accidentally shipped half-hardened. Two coupled changes:

1. **Retire `MULTI_TENANT_ENABLED`.** Tenancy is always present in the data
   model (every row already carries a tenant since #130); a single tenant is
   just the degenerate case where everyone lives in `'default'`. Enforcement
   becomes coextensive with authentication instead of a separate, silently-
   default-off flag.
2. **Add a fail-closed hardened-config boot preflight** (the reframed #36):
   when `AUTH_ENABLED=true`, refuse to boot if a secret that has no safe
   default is missing.

## Background / why

- **The footgun.** `ensure_project_access` (`src/core/ownership.py:18`) begins
  `if not MULTI_TENANT_ENABLED or identity.tenant_id is None: return`. So a
  deployment with `AUTH_ENABLED=true` but `MULTI_TENANT_ENABLED=false` (its
  default) authenticates users and stamps their keys with tenants, yet performs
  **no cross-tenant isolation** — and nothing warns the operator. This is the
  "hardened mode is a lie" class of bug #36 targets.
- **The foundation is already laid.** #130 made `tenant_id` `NOT NULL` with
  `server_default='default'` and backfilled every row. The column is populated
  regardless of any flag; `MULTI_TENANT_ENABLED` now *only* governs whether we
  enforce — which is precisely the switch that shouldn't exist independently of
  auth.
- **`AUTH_ENABLED=true` already suffices for everything else.** Audit of every
  hardening knob shows each has a safe default under auth-on except two secrets:
  `SERVICE_ACCOUNT_JWT_SECRET` (already fail-closed, but lazily — at first token
  use, not at boot) and `API_KEY_PEPPER` (optional; plain-SHA fallback is
  acceptable for 256-bit random keys per the audit). No `CONTEX_SECURE`
  meta-flag is needed — one auth switch, matching Redis/Postgres/Elasticsearch.
- **The existing config validator is orphaned.** `validate_config()` only
  emits warnings and `load_and_validate_config()` is never called from
  `main.py` (which reads env directly via `os.getenv`). There is no real
  boot-time hardening gate today.

## Core model

- **Data:** every row always has a tenant (`'default'` unless assigned). No
  change — already true.
- **Enforcement gate:** tenant isolation is enforced **iff `auth_enabled()`**.
  - Auth **on** → isolation always enforced. Multiple tenants exist only if the
    operator mints keys for distinct tenants; otherwise everyone is `'default'`
    and every ownership check passes trivially (invisible).
  - Auth **off** (demo) → no access control at all, tenancy included. Every
    request runs as the single implicit `'default'` tenant. This is the
    intentional easy-demo posture.
- **Tenant is invisible until a second tenant exists.** No API, header, or
  config surface changes for single-tenant users.

## Component changes

### 1. `src/core/ownership.py`
Replace the flag gate with the auth gate:
```python
from src.core.authz import auth_enabled
...
    if not auth_enabled():
        return
```
Drop the `MULTI_TENANT_ENABLED` import and the `identity.tenant_id is None`
special case: under auth-on, `resolve_identity` always yields a non-null
`tenant_id` (NOT NULL column), so the None branch is dead. Gating on
`auth_enabled()` also sidesteps the `ANONYMOUS_IDENTITY` problem: the demo
identity has `tenant_id=None` and `projects=()`, so running `has_project`
against it would wrongly deny — but ownership never runs in demo because auth
is off.

### 2. `src/core/subscriptions.py`
`_assert_sub_tenant` (line 19):
```python
    if auth_enabled() and row.tenant_id != tenant_id:
        raise PermissionError(...)
```
(Same transform: enforce under auth, no-op in demo. `tenant_id` is always set
under auth.)

### 3. `src/core/tenant_middleware.py`
- Remove the `MULTI_TENANT_ENABLED` module constant and the
  `if not MULTI_TENANT_ENABLED:` skip branch in `dispatch` (lines 42, 103–107).
- `TenantMiddleware` and `TenantQuotaMiddleware` are now always active.
- `_identify_tenant`: keep the auth-on path unchanged (tenant derived
  authoritatively from identity; `X-Tenant-ID` only cross-checked → 403 on
  mismatch). **Change the auth-off branch to force `DEFAULT_TENANT_ID`** and
  drop the unauthenticated `X-Tenant-ID` / `/t/…` tenant selection — demo mode
  no longer pretends to have tenants (per "invisible until you need >1").

### 4. `main.py`
- Remove the `if MULTI_TENANT_ENABLED:` guard around the tenant middleware
  registration (lines 339, 361–364); always add both tenant middlewares.
- Add a hardened-config preflight call in `lifespan`, alongside
  `assert_authz_coverage` and `check_protected_mode`.

### 5. Hardened-config preflight (new: `src/core/hardened_config.py`)
```python
def check_hardened_config() -> None:
    """Fail closed at boot when auth is on but a no-safe-default secret is missing."""
```
- When `auth_enabled()`:
  - `SERVICE_ACCOUNT_JWT_SECRET` unset → **raise RuntimeError** (moves the
    existing lazy fail-closed in `service_accounts._jwt_secret` to boot; the
    lazy check stays as a backstop).
  - `API_KEY_PEPPER` unset → **log a warning** (optional; plain-SHA fallback is
    acceptable). Not a hard failure.
  - `CORS_ORIGINS` contains `*` → log a warning (credentials already coerced off
    by #64; this just surfaces it).
- When auth off: no-op (demo).
- Called once in `lifespan` before serving. Chosen over resurrecting
  `load_and_validate_config()` because `main.py`'s established pattern is direct
  `os.getenv` reads and per-boot check functions.

### 6. Config / docs cleanup (clean break, no back-compat alias)
- `.env.example:139` — remove the `MULTI_TENANT_ENABLED` line.
- `docker-compose.yml` — remove any `MULTI_TENANT_ENABLED`; stays auth-off demo
  (do **not** add `AUTH_ENABLED=true` — auth-off default is intentional).
- README / docs — drop `MULTI_TENANT_ENABLED`; document the hardening story as
  "set `AUTH_ENABLED=true` and provide `SERVICE_ACCOUNT_JWT_SECRET` (required)
  + `API_KEY_PEPPER` (recommended); tenancy turns on automatically."

## Error handling
- Boot preflight raises `RuntimeError` with a message naming the missing env
  var (never its value), consistent with the existing `_jwt_secret` message.
- Cross-tenant access continues to raise the domain `PermissionError` →
  single `@app.exception_handler(PermissionError)` → 403 `{"detail":"Forbidden"}`.
- Tenant-spoofing (X-Tenant-ID ≠ identity) stays 403.

## Testing
- **Rework the ~7 test files** that monkeypatch `MULTI_TENANT_ENABLED` to drive
  the behavior through `auth_enabled()` instead (monkeypatch
  `authz.auth_enabled` / the relevant module reference, or set `AUTH_ENABLED`).
  Files: `test_ownership.py`, `test_route_ownership.py`,
  `test_subscription_ownership.py`, `test_tenant_isolation.py`,
  `test_mcp_subscription_tools.py`, `test_tenant.py`.
- **Semantics flip:** "no-op when `MULTI_TENANT_ENABLED=false`" tests become
  "no-op when auth off"; "enforced when `MULTI_TENANT_ENABLED=true`" become
  "enforced when auth on."
- **New footgun-closed test:** auth on + a second tenant's key → cross-tenant
  access is 403 **without any tenant flag being set** (proves the flag is gone
  and isolation is automatic).
- **Demo test:** auth off → cross-tenant/`X-Tenant-ID` calls are *not* enforced
  and all data lands in `'default'`.
- **Preflight tests:** auth on + no `SERVICE_ACCOUNT_JWT_SECRET` → boot raises;
  auth on + secret set → boots; auth off + nothing set → boots (demo).
  `import main` must still succeed (no import-time evaluation).
- Full suite green (via plain shell — watchdog).

## Out of scope
- **Rate limiting** — still unwired (#38 path bug); not part of this.
- **RLS / DB-level tenant enforcement** — separate later effort.
- **Per-tenant quotas redesign** — `TenantQuotaMiddleware` keeps current
  behavior; it simply always runs now (quotas apply to `'default'` too, which
  is fine for a generous default tenant).

## Migration / compatibility
- No DB migration (tenancy data model unchanged since #130; head stays 007).
- **Behavioral change for existing hardened deployments:** anyone running
  `AUTH_ENABLED=true` + `MULTI_TENANT_ENABLED=false` today gets *no* isolation;
  after this they get isolation automatically (correct, but a behavior change).
  Anyone running `AUTH_ENABLED=true` + `MULTI_TENANT_ENABLED=true` sees no
  change. Given pre-#132 there were no peppered keys and adoption is low, the
  blast radius is negligible; call it out in the PR/release notes.
- Setting `MULTI_TENANT_ENABLED` becomes a no-op env var (removed from docs);
  we do not read it anywhere.
