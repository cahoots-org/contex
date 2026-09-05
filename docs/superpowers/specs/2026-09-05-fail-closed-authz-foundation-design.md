# Fail-Closed Authorization Foundation (Wave D, Tier 0)

- **Date:** 2026-09-05
- **Status:** Approved (2026-09-05) — ready for implementation planning
- **Issues (foundation):** #37, #38, #39, #40, #41, #53, #72
- **Spun off (fast-follow, build on this):** #42, #43, #54, #55, #73
- **Adjacent, out of scope here:** #64 (CORS), #68 (JWT secret), #69 (key hashing), RLS (own issue)

## 1. Problem

Contex ships auth off by default (conventional for self-hosted infra — Redis,
Kafka, NATS, Qdrant all do the same). Wave D is about the *other* mode: when an
operator sets `AUTH_ENABLED=true`, the system must actually be airtight. Today it
is not. The root cause is one architectural choice, and every Wave D security
issue is a symptom of it:

**Authorization is fail-open by construction.** A request is *allowed* unless a
hand-maintained, string-prefix-matched table happens to recognize its path and
deny it. Verified in the current code:

- `ENDPOINT_PERMISSIONS`/`METHOD_PERMISSIONS` keys are all `/api/*`, but the
  canonical router mounts at `/api/v1` (`main.py:353`). `_path_matches` uses
  `str.startswith` (`rbac_middleware.py:70`), so no v1 path matches,
  `get_required_permission` returns `None`, and the request short-circuits to
  *allow* (`rbac_middleware.py:138`). RBAC is a total no-op on all primary routes
  even when auth is on. A `readonly` key can publish, delete, and mint keys. (#38)
- `get_role` returns `READONLY` for any unknown key instead of denying
  (`rbac.py:183`) — fail-open to a role.
- RBAC only runs at all if an `X-API-Key` header is present
  (`rbac_middleware.py:115`).
- `APIKeyMiddleware` and `RBACMiddleware` each maintain their own, *divergent*,
  hardcoded "public path" skip-lists (`auth.py:77` vs `rbac_middleware.py:106`).
  Divergence = holes (`/sandbox` bypass #40; versioning bypass #72).
- The `/mcp` server is a mounted Starlette sub-app (`main.py:274`). FastAPI route
  dependencies and the parent middleware's per-route logic cannot see individual
  JSON-RPC tool calls; its tool handlers perform no auth, tenant, or ownership
  checks at all (#37, #53).
- `RBACMiddleware` reads and re-injects the request body via a manual `receive()`
  shim (`rbac_middleware.py:147-157`) — a recognized `BaseHTTPMiddleware`
  anti-pattern the Starlette maintainers have slated for deprecation.

The fix is to invert the default: a request is *denied* unless the route it hits
has explicitly declared the permission it requires, and that declaration lives
where it cannot drift from the route.

## 2. Goals / Non-goals

**Goals**
- When `AUTH_ENABLED=true`, every REST route and every MCP tool is fail-closed:
  no explicit permission (or explicit public marker) ⇒ denied, and for REST,
  the app refuses to boot.
- One identity model, resolved once, shared by REST and MCP.
- Tenant/project scope derives from the authenticated identity, never from
  client-supplied arguments.
- A protected-mode guard so the auth-*off* default can't be accidentally exposed
  to remote callers.

**Non-goals (deliberately deferred)**
- Postgres RLS (own issue) — we design the key→tenant binding so RLS slots in
  later, but do not implement it here.
- The mechanical ownership/IDOR checks (#42/#43/#54/#55/#73) — fast-follow issues
  once identity is trustworthy.
- CORS (#64), JWT-secret durability (#68), salted key hashing (#69) — adjacent
  small hardening PRs.
- OAuth 2.1 / full MCP spec compliance (RFC 9728 discovery, RFC 8707 audience,
  PKCE, DCR). The `TokenVerifier` abstraction keeps this a later one-file swap.

## 3. Research basis (2026-09-05)

Three parallel research passes (FastAPI authz patterns; MCP auth spec + our SDK;
comparable-infra RBAC posture) converged with no contradictions.

- **FastAPI:** community + production consensus is authorization via `Depends`,
  not middleware. Middleware authz decouples policy (a path table) from the route
  it protects, so they drift — exactly our bug. But route dependencies alone are
  still fail-*open* on a forgotten route; a **startup coverage assertion** is what
  makes it fail-closed. Mounted sub-apps are independent ASGI apps: route
  `Depends` and even a naive coverage walker skip them, so `/mcp` must be handled
  explicitly. `BaseHTTPMiddleware` that reads the body is a known anti-pattern.
- **MCP (`mcp==2.0.0`, the official LF SDK — verified installed):** exposes an
  auth surface — `mcp/server/auth/provider.py` (`TokenVerifier`),
  `.../settings.py` (`AuthSettings`), `.../middleware/bearer_auth.py`,
  `.../middleware/auth_context.py` (`get_access_token`); `MCPServer` also exposes
  `middleware` and `custom_route` hooks. `TokenVerifier.verify_token(token) ->
  AccessToken | None` is auth-scheme-agnostic: an API-key verifier returns the
  same `AccessToken{scopes, claims}` shape a JWT verifier would. The spec stops at
  "valid token for this server"; per-tool authz and tenant isolation are
  application logic we write (default-deny), deriving tenant/project from token
  **claims, not tool arguments**.
- **Comparable infra:** auth-off-by-default is conventional for local/dev, but
  modern engines add a fail-closed guard for remote exposure (Redis protected
  mode, 2016; MongoDB bind_ip, 2017; ES 8.0 secure-by-default, 2022). Our four
  roles (admin/publisher/consumer/readonly) are conventionally sized — do not add
  more. The universal IDOR weakness is trusting a client-supplied tenant/namespace
  (Pinecone, Turbopuffer); the fix is binding the key to a tenant server-side.
  Fail-closed-on-ambiguity is the documented standard (OWASP "fail securely",
  NIST SP 800-53 AC-6/SA-8 deny-by-default).

## 4. Design

### 4.1 One identity resolver

A single module resolves a bearer credential to an `Identity`, or denies:

```
Identity {
    key_id: str
    scopes: frozenset[Permission] # AUTHORITATIVE grant — the source of truth for authz
    tenant_id: str | None         # bound at key issuance; the tenant assertion
    projects: list[str]           # empty = all projects within tenant (whole-key scope)
    role: Role | None             # preset label that seeded scopes; not consulted at check time
}
```

**Scopes are authoritative; roles are presets.** Authorization is checked against
`identity.scopes` (`require(P)` tests `P ⊆ scopes`) — never against the role
directly. A role is just a convenient way to populate scopes. This gives
capability-precise keys ("publish but not query") without inventing a new role
per combination.

- `resolve_identity(db, credential) -> Identity | None` — looks up the key by
  hash and computes scopes as: **`APIKey.scopes` if non-empty, else expand the
  assigned role** via `ROLE_PERMISSIONS`. Returns a fully-populated `Identity`, or
  `None` (deny). **Unknown/invalid key ⇒ `None`, never a default role.** This
  replaces `get_role`'s fail-open `READONLY` default (`rbac.py:183`).
- **No migration.** `APIKey.scopes` already exists (`db_models.py:110`) but is
  currently dead for authz; this makes it authoritative. `ServiceAccount` already
  carries `role`+`scopes`+`allowed_projects` (`db_models.py:154-156`) — the same
  scope-based grant, so key and service-account resolution align.
- **Two ways to mint a key:** *preset* (assign a role; empty scopes → role
  expands at resolve time, so existing keys keep working) or *custom* (store an
  explicit permission list in `APIKey.scopes`). The admin API to mint
  custom-scope keys is a small addition (or fast-follow); the resolver + all
  enforcement support it from day one.
- **Deferred:** per-project-*differentiated* grants (e.g. admin on project A,
  readonly on B within one key). `projects` scopes the whole key uniformly for
  v1; differentiated grants are the capability-token model — own issue, not now.
- Both entry points below call this one resolver — no more divergent skip-lists
  or split identity logic.
- **Tenant comes from the key, not the request (the #41 fix).**
  `identity.tenant_id` is read from the API-key record. The `X-Tenant-ID` header
  (today consumed by `TenantMiddleware`, `main.py:334`) must not select a tenant
  when auth is on: it is either dropped or validated to equal
  `identity.tenant_id`, rejecting a mismatch with 403. A client can never assert a
  tenant it wasn't issued.

#### 4.1.1 Permission vocabulary must cover every subsystem

Granularity is bounded by the `Permission` enum. Keep it *per-capability*, not
per-URL (one `manage_webhooks`, not eleven). The current enum (`rbac.py:24`) only
covers the core `routes.py` surface; the auxiliary subsystems have **no
permissions defined**:

- **Webhooks** (11 routes), **service accounts** (9), **tenant CRUD** (10),
  **audit** (5), **versioning** (4) — all currently unmapped.

Because the coverage gate forbids an un-annotated route, defining permissions for
these subsystems (and mapping them into roles — `admin` gets all; others as
appropriate) is in-scope work here. This is the gate doing its job: versioning
shipping with no authz *is* #72. Expect to add roughly one capability-permission
per subsystem plus a few finer ones (e.g. separate read vs. manage for audit).

### 4.2 REST: route dependencies + boot-time coverage gate

**Authentication** shrinks to one concern: a dependency (`get_identity`) that
reads the credential, calls `resolve_identity`, and 401s if absent/invalid. It
does **not** read the request body. The body-reading `RBACMiddleware` and its
`receive()` shim are removed.

**Authorization** is declared on the route:

```python
def require(*permissions: Permission):
    async def _dep(identity: Identity = Depends(get_identity)) -> Identity:
        if not set(permissions).issubset(identity.scopes):
            raise HTTPException(403, ...)   # valid identity, insufficient permission
        return identity
    _dep._authz_marker = permissions        # sentinel for the coverage walker
    return _dep

@router.post("/publish", dependencies=[Depends(require(Permission.PUBLISH_DATA))])
async def publish(...): ...
```

Routes the handler needs the identity for use `identity: Identity =
Depends(require(...))`; routes that only gate use `dependencies=[...]`.

**Public routes are explicit.** `/health`, `/`, docs/openapi, static, and the
sandbox UI are marked with an explicit `public` marker dependency (used as
`dependencies=[Depends(public)]`, not an implicit skip-list). Nothing is public by
omission.

**Remove the legacy `/api` alias.** The deprecated `/api` mount (`main.py:391`)
and its `DeprecationWarningMiddleware` are deleted outright — it was the only
prefix the old broken permission table matched, and this project is too immature
/ low-adoption to carry legacy surface. Only `/api/v1` remains.

**Boot-time coverage gate (the fail-closed keystone).** During lifespan startup
(and as a CI test), walk `app.routes`:

- For each `APIRoute`: it must carry a `require(...)` marker in its dependency
  tree **or** a `public` marker. Otherwise collect it.
- For each `Mount` (e.g. `/mcp`, `/static`): must be on an explicit reviewed
  allowlist, since the walker cannot see inside it.
- Handle `APIWebSocketRoute` explicitly (no HTTP dependency ergonomics).
- If anything is uncovered, **raise and refuse to boot**, listing the offenders.

This makes "a new route with no decision" a boot failure in CI, not a production
hole. It closes #38 (bypass), #39 (the broken admin guard becomes a real
per-route `require(MANAGE_*)`), and the coverage half of #37/#40/#72.

The admin-guard bug #39 (guards read the wrong `request.state` attribute) is
resolved structurally: admin routes carry `require(Permission.<admin perm>)` like
any other, reading identity through the DI graph rather than an ad-hoc state
attribute.

#### 4.2.1 Coverage gate — detailed design

**Detection mechanism.** `require(...)` returns a fresh dependency closure per
call; it is tagged with a sentinel attribute (`_authz_marker`). `public` is a real
no-op dependency tagged `_public_marker`. The gate walks `app.routes`, and for
each `APIRoute` recursively flattens `route.dependant.dependencies`, checking
whether any callable in the tree carries either sentinel. Covered = has `require`
**or** `public`; uncovered routes are collected. This is per-route *and*
per-method (GET /x can be public while POST /x is protected), and the marker
lives on the route, so it cannot drift from the endpoint — the whole point of
moving off the string-prefix table.

**Hybrid allowlist for un-annotatable routes.** Two kinds of routes we do not
define and therefore cannot annotate need a short, explicit, *reviewed* by-path
allowlist:
- **Framework-owned:** `/openapi.json`, `/docs`, `/docs/oauth2-redirect`,
  `/redoc`.
- **Mounts:** `/mcp` (covered by its own SDK auth, §4.3) and `/static` (genuinely
  public). The walker cannot see inside a `Mount`; each mount path is on the
  allowlist, and an *unknown* mount appearing later → boot failure.

This "per-route sentinels for our routes + a small reviewed allowlist for what we
can't annotate" hybrid is the honest shape; pretending it's pure would just hide
the mount/docs routes.

**Always on.** The gate runs unconditionally, independent of `AUTH_ENABLED`.
Coverage is a structural property of the route table, so checking it always
guarantees that the first time an operator flips auth on in prod, no uncovered
route is waiting. It is a cheap startup walk.

**Runs in two places:** (1) the lifespan startup — raises and refuses to boot,
listing offenders; (2) a standalone pytest calling the same function — fast CI
feedback without spinning the full app. No WebSocket routes exist today; the
walker handles `APIWebSocketRoute` defensively so one added later without a marker
is caught.

### 4.3 MCP: SDK identity + default-deny per-tool

Wiring verified against the installed `mcp==2.0.0` source (not 1.x docs).

- **Enable bearer auth with no OAuth machinery.** Pass to `MCPServer(...)` in
  `src/core/mcp_adapter.py`:
  - `token_verifier=<ApiKeyVerifier>`
  - `auth=AuthSettings(issuer_url="https://contex.local", resource_server_url=None,
    required_scopes=None)`
  - leave `auth_server_provider=None`.
  `streamable_http_app()` then auto-installs `AuthenticationMiddleware(
  BearerAuthBackend)` + `AuthContextMiddleware` and wraps the `/mcp` route in
  `RequireAuthMiddleware` (401 missing/invalid, 403 insufficient scope). We add no
  middleware manually. `resource_server_url=None` keeps us off the RFC 9728
  discovery path; `issuer_url` is required by pydantic but never read in this path.
  (`AccessToken` imports from `mcp.server.auth.provider`, not `mcp.shared.auth`.)
- **`ApiKeyVerifier` implements the `TokenVerifier` Protocol:**
  `async verify_token(token) -> AccessToken | None`. It calls the shared
  `resolve_identity` and returns `AccessToken(token=..., client_id=key_id,
  scopes=[p.value for p in identity.scopes], claims={"role", "tenant_id",
  "projects"})`, or `None` to reject. Same `Identity`, different envelope.
- **Lazy DB access (gotcha).** The MCP server is built at module import
  (`main.py:272`), before the DB exists. The verifier must resolve keys **lazily**
  against `app.state.db` at call time — mirror the existing lazy engine accessor
  in `build_mcp_server`. Do not capture a static key set.
- **Per-tool default-deny.** The SDK's `required_scopes` is a coarse route-level
  gate; per-tool granularity lives in-handler. Each tool carries a
  `@requires(Permission.X)` check reading `get_access_token()` from
  `mcp.server.auth.middleware.auth_context`. No tool ships without a declared
  permission. Maps: `contex_query`→`QUERY_DATA`, `contex_publish`→`PUBLISH_DATA`,
  `contex_create_subscription`/`contex_delete_subscription`/`read_subscription`
  to their respective permissions.
- **Tenant/project from claims, not arguments.** Handlers currently take
  `project_id` as a tool argument (`mcp_adapter.py:38,45,65`). The authorization
  check validates that argument against the identity's `tenant_id`/`projects` from
  the token claims; a project outside the identity's scope is denied. This closes
  #53 and the MCP half of #54.
- **Lifespan unchanged.** `main.py:243` already does
  `async with _mcp_server.session_manager.run():`; adding auth does not change the
  mount/lifespan wiring — keep it exactly as is.
- When `AUTH_ENABLED=false`, `token_verifier`/`auth` are not passed (parity with
  REST: demo mode is open). Protected mode (§4.5) still applies at the connection
  layer.

### 4.4 Remove fail-open defaults (summary)

| Current fail-open | New fail-closed |
|---|---|
| `get_role` → `READONLY` for unknown key | `resolve_identity` → `None` (deny/401) |
| Unmapped route → allow | Uncovered route → boot failure |
| RBAC skipped when no `X-API-Key` | `get_identity` 401s on missing credential |
| Divergent per-middleware skip-lists | One explicit `public` marker + resolver |
| MCP tools → no checks | Per-tool default-deny + claims-scoped tenant |

### 4.5 Protected mode (auth-off safety)

Independent of `AUTH_ENABLED`. When auth is **unconfigured** (off) and the bind
address is non-loopback, refuse remote connections (Redis-protected-mode
precedent). The common Contex deployment is a cluster with no external ingress;
this enforces that assumption and catches accidental exposure. Configurable
escape hatch (e.g. `CONTEX_PROTECTED_MODE=false`) for operators who intentionally
run open. Emits a loud startup warning either way.

## 5. Data flow

**REST (auth on):** request → `get_identity` dependency resolves `Identity` or
401 → `require(...)` dependency checks permission ⊆ scopes, else 403 → handler
runs with `Identity` in hand, scoping any project access to `identity.projects`.

**MCP (auth on):** request → SDK bearer middleware calls `TokenVerifier` →
`AccessToken` (or 401) placed in auth context → tool handler's `@requires` checks
scope (403 on miss) → handler validates the `project_id` argument against the
identity's tenant/projects before touching data.

**Boot:** lifespan → run migrations (existing) → **coverage gate** (new; refuse
boot if any route uncovered) → serve.

## 6. Error handling

- **401** missing/invalid/expired credential (both transports).
- **403** valid identity, insufficient permission or out-of-scope tenant/project.
- **Boot failure** (not a request error) when route coverage is incomplete —
  surfaced in CI.
- Auth/policy backend unreachable ⇒ deny (fail secure), never allow.

## 7. Testing strategy

- **Coverage gate as a unit/CI test** — asserts every route is covered; this is
  the regression guard that keeps the system fail-closed as routes are added.
- **Per-route authz matrix** — for representative routes, assert each role gets
  200/403 as expected, using `app.dependency_overrides` to inject identities.
- **Negative auth** — unknown key → 401 (not readonly); missing header → 401;
  cross-project/tenant `project_id` → 403.
- **MCP tool tests** — each tool denies without the required scope and denies a
  `project_id` outside the identity's scope.
- **Protected mode** — remote connection refused when unconfigured + non-loopback;
  loopback allowed; escape hatch honored.
- **No new failures in `tests/`.** (~6 known-unrelated `sdk/python/tests`
  failures are pre-existing and ignored.)

## 8. Migration / rollout

- **No DB migration.** `APIKey.tenant_id` and `APIKeyRole.tenant_id` already
  exist (`db_models.py:111,151`); the foundation enforces and reads them. Alembic
  head stays at `006`.
- Behavior change is gated by `AUTH_ENABLED`; the default (off) demo path is
  unchanged except for the new protected-mode guard.
- **Breaking (intended):** with auth on, keys with no role assignment are denied
  rather than treated as readonly, and every route now enforces.
- **Bootstrap lockout — already handled, preserve it.** The bootstrap path assigns
  `Role.ADMIN` in both the provided-key (`main.py:121`) and auto-generated
  (`main.py:127`) branches, so the operator is not locked out under
  deny-by-default. Implementation must not regress this.
- **Legacy `/api` alias removed** (`main.py:391` + `DeprecationWarningMiddleware`).
  Clients on the deprecated prefix must move to `/api/v1`. Acceptable given
  current adoption.

## 9. Issues closed vs spun off

- **Closed by this foundation:** #37, #38, #39, #40, #41 (tenant from identity),
  #53, #72.
- **Fast-follow (trivial once identity is trustworthy):** #42 (bundle ownership),
  #43 (key listing tenant scope), #54 (cross-project), #55 (cleanup authz),
  #73 (subscription ownership). File as issues referencing this spec.
- **Separate:** RLS; #64; #68; #69.

## 10. Risks / open questions

- **Coverage-walker precision** — detecting the `require` marker in the resolved
  `Dependant` tree and correctly enumerating `Mount` routes needs care; get this
  right or the gate gives false confidence. Detailed design in §4.2.1. (No
  WebSocket routes exist today; the walker handles them defensively.)
- **MCP SDK auth ergonomics — resolved.** Wiring verified against installed
  `mcp==2.0.0` source; concrete config in §4.3. Remaining implementation care:
  the lazy DB accessor in the verifier, and confirming the coarse
  `required_scopes` vs in-handler `@requires` split behaves as expected under a
  real streamable-HTTP session.
