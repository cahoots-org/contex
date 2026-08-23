# Contex Adversarial Audit — Final Report

## Executive Summary

Contex ships with authentication, RBAC, multi-tenancy, and rate limiting **off by default** (`AUTH_ENABLED=false`, `MULTI_TENANT_ENABLED=false`). This single design choice is both the top critical finding and the reason most other security findings are downgraded: in the default configuration there is no privilege boundary to bypass, so many "cross-tenant" and "RBAC bypass" issues are latent — they only bite operators who deliberately turn security on and then discover it doesn't work as intended. The audit's most damning pattern is exactly that: **several controls that operators enable to harden production are silently inoperative** (RBAC path-prefix mismatch, admin guard reading a never-set attribute, sandbox/MCP auth bypass, dead salt config).

### Counts by dimension × severity (kept findings, post-skeptic severities)

| Dimension | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| Security | 2 | 8 | 11 | 8 | 29 |
| Onboarding friction | 0 | 3 | 5 | 8 | 16 |
| Oversights | 0 | 3 | 6 | 11 | 20 |
| **Total** | **2** | **14** | **22** | **27** | **65** |

(Info-level "clean surface" notes and the 6 refuted items are excluded from the table; refuted items appear in the appendix.)

### Top 5 to fix first, in order

1. **Auth/RBAC/rate-limiting off by default** — the shipped `docker-compose.yml` exposes data-plane *and* key-management (`POST /api/v1/auth/keys`) and admin-cleanup endpoints unauthenticated on all interfaces. Flip `AUTH_ENABLED` default to true (or gate an explicit dev flag) and add `AUTH_ENABLED=true` to compose.
2. **Fix the RBAC `/api/v1` path-prefix mismatch** — every permission pattern targets `/api/*` but routes are mounted at `/api/v1/*`, so RBAC is a total no-op on all primary routes even when auth is enabled. A readonly key can publish, delete, and mint API keys.
3. **Fix the admin guard attribute-name bug** — all four `require_admin_permission` guards read `request.state.api_key_role`, which is never set; the middleware sets `request.state.role`. Result: a complete admin-tier bypass across ~28 endpoints (tenant CRUD, audit logs, service accounts, webhook management) when auth is on. Add a test asserting non-admin keys get 403.
4. **Add SSRF protection to webhook URLs** — both the managed webhook endpoint and agent-registration paths accept arbitrary URLs (incl. `169.254.169.254`) with no IP/scheme allowlist, reachable unauthenticated in default config. Resolve+reject RFC-1918/loopback/link-local before delivery.
5. **Fix broken infra defaults in shipped deployment configs** — unauthenticated Redis on `0.0.0.0` carrying live context data (not just IDs), OpenSearch security plugin disabled on all interfaces, hardcoded Postgres password. These are three separate high/medium findings all triggered by `docker compose up`.

---

## Security

### Critical

**Authentication, RBAC, and rate limiting are OFF by default** — critical — `main.py:318-332`, `.env.example:54`, `docker-compose.yml:60-69` — When `AUTH_ENABLED=false` (the shipped default), `APIKeyMiddleware`, `RBACMiddleware`, and `RateLimitMiddleware` are never added; every REST endpoint plus `/mcp` is reachable by any unauthenticated client, including `POST /api/v1/auth/keys` (mint a key), `POST /api/v1/data/publish`, `GET /projects/{id}/data`, and `POST /api/v1/admin/cleanup`. The shipped `docker-compose.yml` sets no `AUTH_ENABLED`, so `docker compose up` is fully open on port 8001. *(Reported by sec-tenant, sec-data, sec-config — merged.)* Fix: default `AUTH_ENABLED=true`; fail fast (or emit a logged error, not a print) when binding a non-loopback interface with auth off; add `AUTH_ENABLED=true` to compose.

**`/mcp` endpoint fully unauthenticated in default config** — critical — `main.py:275, 318-332`; `src/core/mcp_adapter.py:37-72` — The MCP server is mounted at `/mcp` and, with auth off by default, all five tools (`contex_query`, `contex_publish`, `contex_create_subscription`, `contex_delete_subscription`, `read_subscription`) accept arbitrary `project_id`/`subscription_id` with no session identity and no object-level checks. Note the skeptic corrected one finder's framing: the middleware does NOT architecturally bypass the mount in Starlette 1.6.0 — the exposure is purely because no auth middleware is registered by default. *(sec-mcp rated critical; sec-tenant's variant of the same issue was rated high. Kept at critical for the default-config, no-identity case.)* Fix: authenticate the MCP transport (Bearer handshake), propagate principal/tenant into handlers, and enforce ownership.

### High

**RBAC permission patterns don't match `/api/v1` routes — total RBAC bypass when auth is on** — high — `src/core/rbac_middleware.py:11-44` — All `ENDPOINT_PERMISSIONS`/`METHOD_PERMISSIONS` patterns use bare `/api/*` prefixes, but the canonical router mounts at `/api/v1` (`main.py:354`); `_path_matches` uses `startswith`, so no v1 path matches and `get_required_permission` returns `None`, short-circuiting to `call_next` with no check. Same mismatch disables per-endpoint rate limits (`rate_limiter.py:163-169`). Only the deprecated `/api` alias matches. *(Reported by sec-tenant and onb-coldstart — merged.)* Fix: prefix all patterns with `/api/v1/`, or enforce RBAC via FastAPI `Depends` at the router level; add a startup assertion that every route has a permission entry or explicit exemption.

**Broken admin-permission guard across all admin routers (wrong state attribute)** — high — `src/api/tenant_routes.py:109`, `audit_routes.py:70`, `webhook_routes.py:153`, `service_account_routes.py:139` vs `src/core/rbac_middleware.py:178-179` — Guards read `request.state.api_key_role` (never set anywhere); middleware sets `request.state.role`/`role_assignment`. `getattr(...) → None` hits the "no role" branch, logs a warning, and returns without raising — so any caller passes. Affects ~28 endpoints: tenant CRUD, audit read/export, webhook CRUD + secret-rotation, service-account management. Independently exploitable with `AUTH_ENABLED=true` and any valid non-admin key. *(Reported by sec-tenant, sec-web, sec-webhook, onb-coldstart — merged; original criticals corrected to high because default config has no auth to bypass.)* Fix: read `request.state.role_assignment` and check `.role == Role.ADMIN`; add a non-admin-gets-403 test per endpoint.

**Sandbox routes bypass auth even when `AUTH_ENABLED=true`** — high — `src/core/auth.py:81`; `src/web/routes.py:222-262, 265-285` — `/sandbox` is hardcoded in `APIKeyMiddleware.public_paths`, so `GET /sandbox/projects/{id}/data` (dumps full raw embeddings payloads for any project), `/sandbox/subscribe`, and `/sandbox/query` remain unauthenticated after an operator enables auth — silently defeating the control they turned on for exactly this data. *(Reported by sec-web and onb-coldstart — merged.)* Fix: remove `/sandbox` from `public_paths` when auth is enabled, or gate the sandbox behind a `SANDBOX_ENABLED` flag defaulting off in production.

**X-Tenant-ID header accepted without ownership validation (tenant spoofing)** — high — `src/core/tenant_middleware.py:182-185` — When `MULTI_TENANT_ENABLED=true`, tenant is resolved from the `X-Tenant-ID` header as the highest-priority method, verbatim, before the API-key→tenant lookup (which is then skipped). Any authenticated caller can impersonate any tenant and read/write its data. Corrected from critical to high: requires the non-default `MULTI_TENANT_ENABLED=true` (and realistically `AUTH_ENABLED=true`). Fix: resolve tenant exclusively from the API key's recorded tenant; validate against the project's owning tenant before responding. *(The related low-severity path-prefix `/t/{tenant_id}/` variant at `tenant_middleware.py:196-200` is unverified but should be fixed in the same pass.)*

**Subscription bundle IDOR: `get_bundle`/`delete` accept arbitrary IDs with no ownership check** — high — `src/core/subscriptions.py:36-53`; `src/core/mcp_adapter.py:57-62` — Both query by `subscription_id` alone (no tenant/project filter). IDs are 128-bit UUIDs but are broadcast on Redis channel `subscription:{id}:updated`, and in default config the MCP endpoint is unauthenticated so any HTTP client can read or delete any subscription. The defect survives auth being enabled — the fix must be in the service layer. Fix: add `tenant_id`/`project_id` to the WHERE clause, sourced from authenticated state.

**API key listing/revocation lack tenant scoping** — high — `src/api/routes.py:140-193`; `src/core/auth.py:237-265` — `GET /auth/keys` calls `list_api_keys(db)` with no tenant filter (returns all keys); `DELETE /auth/keys/{key_id}` revokes without an ownership check. With auth on + multi-tenant on, any valid key can enumerate and revoke any other tenant's admin key. In default config these endpoints need no credential at all. Fix: pass `request.state.tenant_id` to the listing; verify target-key tenant matches caller before revoke.

**Webhook SSRF via unvalidated URL (managed endpoints + agent registration)** — high — `src/api/webhook_routes.py:38`; `src/core/webhooks.py:392, 486, 716`; `src/core/models.py:45`; `src/core/webhook_dispatcher.py:156` — `CreateEndpointRequest.url` is a plain `str` (no scheme/IP validation); `AgentRegistration.webhook_url` uses `HttpUrl` which blocks `file://` but still allows private IPs. Delivery POSTs to the stored URL with no allowlist. Reachable unauthenticated in default config (admin guard is a no-op). Blind-SSRF/internal-scan primitive; response body (≤500 chars) is stored and retrievable via the deliveries log, but only on non-2xx, so IMDSv1 (200) credential exfil is not automatic — hence high, not critical. *(Reported by sec-data, sec-web, sec-webhook, onb-coldstart — merged; two finders' criticals corrected to high.)* Fix: parse with `AnyHttpUrl`; resolve hostname and reject RFC-1918/loopback/link-local/metadata ranges before connecting.

**Redis has no authentication and carries live context data** — high — `docker-compose.yml:20-29`, `helm/contex/values.yaml:135-137`, `k8s/base/configmap.yaml:11` — Redis runs with no password; compose publishes 6379 on `0.0.0.0`. `context_engine.py:480-507` shows pub/sub payloads contain the full data dict, not just wake-up IDs. `redis-cli -h host PSUBSCRIBE '*'` streams real cross-project context with no credential. Corrected from medium to high on the basis that the channel carries actual content. Fix: enable `--requirepass`, add password to `REDIS_URL`, bind to the internal network only.

**OpenSearch security plugin disabled on all interfaces** — high — `docker-compose.yml:40, 36-37` — `plugins.security.disabled=true` on ports 9200/9600 bound to `0.0.0.0` gives unauthenticated read/write/delete over all vector indices (`curl host:9200/_cat/indices`, `DELETE /contex-*`). Only active when the non-default hybrid-search backend is used, but the compose file sets `HYBRID_SEARCH_ENABLED=true`. Fix: remove the disable flag, add basic auth, bind to loopback/internal network. (The `opensearch/Dockerfile` NOPASSWD-chown sub-issue applies only to the Railway build path, not compose — see low-severity list.)

**API key hashing uses unsalted SHA-256; `API_KEY_SALT` is validated but never applied** — corrected to medium (see below). *(Listed here because two finders filed it as high; skeptic corrected both to medium given 192–256-bit `secrets.token_urlsafe` keys make rainbow tables infeasible.)*

### Medium

**MCP `contex_publish` stamps `source='api'` instead of `'mcp'`** — medium — `src/core/mcp_adapter.py:67-69` — Handler calls `publish_data(...)` without `source=`, so MCP events default to `source='api'`, corrupting transport attribution in the `events` table. Low real impact today because actor fields are null by default. Fix: pass `source='mcp'` (and resolved actor once available in Layer 1b).

**Batch-publish and file-upload publish paths omit provenance actor** — medium — `src/api/routes.py:591, 1323` — Unlike the primary publish route, these call `engine.publish_data(event)` with no `source`/`actor`/`tenant_id`, so authenticated publishes persist with null provenance. Fix: apply the same `_get_request_context` + actor-stamping pattern.

**MCP tool handlers perform no auth/tenant/project-ownership checks** — medium (needs confirmation on Starlette bypass claim) — `src/core/mcp_adapter.py:37-72`; `main.py:274-275` — All five tools accept arbitrary IDs with no authorization. Corrected from high to medium: in default config not differentially worse than the rest of the open API; becomes meaningful when an operator enables auth expecting `/mcp` to be protected. Fix: enforce object-level ownership in handlers. *(Overlaps the critical `/mcp` finding above; the auth-transport gap is tracked there, the object-level gap here.)*

**Cross-project IDOR: `project_id` is user-supplied with no ownership check** — medium — `src/api/routes.py:791-1081`; `src/core/event_store.py:85-138` — Project-scoped read/query/import/batch endpoints query the DB with the URL/body `project_id` and never compare it to the caller's tenant. Corrected from high to medium: only exploitable with both `AUTH_ENABLED=true` and `MULTI_TENANT_ENABLED=true`; default config has no ownership model. Fix: enforce `get_project_tenant(project_id) == request.state.tenant_id` at a dependency layer.

**Admin cleanup endpoints have no authorization and accept arbitrary project IDs** — medium — `src/api/routes.py:1084-1172` — `POST /admin/cleanup`, `/admin/cleanup/{project_id}`, `GET /admin/retention/{project_id}` have no auth dependency; RBAC patterns `/api/admin/*` don't match the `/api/v1` paths, so `SYSTEM_CLEANUP` is never enforced even with auth on. Blast radius limited to retention-expired events (not arbitrary data). *(sec-tenant filed as medium; sec-data filed the same as "medium" but skeptic corrected sec-data's variant to high for the auth-on case. Merged; kept medium overall, noting the auth-on case is effectively high.)* Fix: add an admin-role dependency; scope project cleanup to the caller's tenant.

**Verbatim exception messages in HTTP 500 responses** — medium — `src/api/routes.py:137,147,192,264,284,344,488,649,744,891,1081,1113,1271` and across `audit_/service_account_/version_/webhook_routes.py` (25+ sites) — `HTTPException(500, detail=str(e))` leaks SQLAlchemy query text, bound params, table/column/constraint names to any caller; unauthenticated in default config. Corrected from high to medium: SQLAlchemy masks DB passwords (`***`), so this is schema disclosure, not credential leak. Fix: register a global `app.exception_handler(Exception)` returning a generic message + request_id; log details server-side.

**Race condition on shared `SemanticDataMatcher` state in concurrent queries** — high (security-dimension filing) / see also Oversights — `src/core/context_engine.py:562-582`, `src/core/matcher.py:40-48`, `src/web/routes.py:64-65` — Concurrent `contex_query`/`/sandbox/query` calls mutate the singleton's `max_matches`/`threshold` around an `await`, so interleaved coroutines read each other's values, silently returning wrong result counts/thresholds. *(Filed under security by sec-mcp at high and under oversights by ovs-correctness at medium — see the Oversights section for the deduplicated entry; severity there corrected to medium.)*

**Unbounded `top_k` in `contex_query`/`contex_create_subscription`** — high — `src/core/mcp_adapter.py:38-46`; `src/core/context_engine.py:542-582` — No upper bound; `top_k=2_000_000` becomes `.limit(4_000_000)` on a pgvector query. Unauthenticated in default config; the REST `QueryRequest` guard (`ge=1, le=50`) is not reached from MCP. Fix: clamp in the tool handlers.

**Unbounded `needs` list amplifies per-publish reconcile cost** — high — `src/core/mcp_adapter.py:45-49`; `src/core/subscriptions.py:55-94` — `reconcile_project` runs O(S×N) synchronous `model.encode()` calls inline on the event loop on every publish; no per-project subscription cap, no rate limit in default config. N=100 subs × M=100 needs = 10,000 blocking inferences per publish. Fix: cap `len(needs)` and subscriptions/project; move reconcile to a background worker.

**MCP sessions accumulate with no idle timeout** — medium — `main.py:274`; `mcp_adapter.py:31` — `streamable_http_app()` is called without `session_idle_timeout`, defaulting to `None`; abandoned sessions are never reaped. Unauthenticated clients can loop `initialize` to exhaust `_server_instances`. Note: the fix requires constructing `StreamableHTTPSessionManager` directly (the param isn't accepted by `streamable_http_app()` in this SDK).

**User-controlled Redis pub/sub channel name** — medium — `src/core/context_engine.py:319-321,431-432,507`; `src/core/models.py:39-42` — `notification_channel` is free-form and published verbatim; a caller can register a victim's channel to eavesdrop on/duplicate notifications for shared data keys (payload injection is not possible — payloads are system-generated). Fix: derive the channel server-side from authenticated identity, or validate against a strict regex.

**Agent registration overwrites any existing agent by ID** — medium — `src/core/context_engine.py:329-342` — `self.agents[agent_id] = {...}` is unconditional; any caller can re-register a victim's `agent_id`, redirecting its webhook to an attacker URL. In-memory store, single-tenant default bounds blast radius. Fix: prove ownership on update, or generate `agent_id` server-side.

**CORS wildcard rate-limiting mismatch: per-endpoint limits are dead when enabled** — medium — `main.py:318-321`; `src/core/rate_limiter.py:163-169` — Rate limiting is gated behind `AUTH_ENABLED` (off by default) and, when on, the `/api/publish`-style patterns don't match `/api/v1/*`, so everything falls to the flat 60/min DEFAULT — the expensive embedding endpoints get no special cap. *(Reported by sec-web and onb-docs — merged.)* Fix: decouple rate limiting from auth; correct patterns to `/api/v1/*`.

**Unbounded `?count=` on `GET /events`** — high — `src/api/routes.py:792`; `src/core/event_store.py:118` — `count: int = 100` has no `le=` bound; passed straight to `.limit(count)` and `.scalars().all()` materializes everything. Unauthenticated by default. Fix: `Query(default=100, ge=1, le=10000)`.

**Unbounded batch endpoints (compute/memory DoS)** — high — `src/api/routes.py:1279, 1365` — `List[DataPublishEvent]`/`List[AgentRegistration]` with no size cap; each item runs synchronous embedding inference on the event loop; no rate limit in default config and batch paths don't match the limiter patterns anyway. *(Reported by sec-data, sec-web, ovs-correctness — merged; ovs-correctness's medium corrected to high.)* Fix: cap list length (`max_length=100`) and per-item data size.

**File upload reads full body into memory before size check** — medium — `src/api/routes.py:559-564` — `content = await file.read()` precedes the `MAX_UPLOAD_SIZE` check; no ASGI-level body limit. Concurrent large uploads exhaust memory; unauthenticated by default. *(Reported by sec-web, sec-webhook, ovs-correctness — merged.)* Fix: stream in chunks with a running total, or set an ASGI body limit.

**OpenSearch TLS verification permanently disabled** — corrected to low (see Low list).

**Hardcoded default Postgres credentials + port on 0.0.0.0** — medium — `docker-compose.yml:6,10,61`; `src/core/config.py:14,122` — Password `contex_password` is hardcoded in compose, config defaults, and repeated across README/docs; 5432 is published on all interfaces. `docker compose up` yields well-known-credential DB access. Fix: remove hardcoded defaults, use secrets, bind 5432 to loopback.

**XML billion-laughs entity expansion (stdlib ElementTree)** — medium — `src/core/parsers/xml_parser.py:38,54` — Uses `xml.etree.ElementTree`; Python 3.12's cap blocks the largest bombs but mid-range (~120KB) expansions parse freely, and `can_parse()`+`parse()` double-parse the document. Unauthenticated DoS by default. Fix: switch to `defusedxml`; make `can_parse()` structural rather than a full parse. *(Note: this parser module is separately flagged as dead code under Oversights — if deleted, this finding is moot.)*

**CORS wildcard origin + allow-credentials by default** — medium — `main.py:278-293`; `src/core/config.py:51-53` — Ships `CORS_ORIGINS=["*"]` with `CORS_ALLOW_CREDENTIALS=True`; Starlette echoes the request Origin with credentials. Low-risk *specifically because* auth is off (no cookies/sessions to steal) and the app authenticates via headers, not cookies — but it is a real footgun that activates on any origin-narrowing change. *(Reported by sec-web, sec-mcp, sec-config, onb-coldstart, ovs-dead — several finders; skeptic corrected most to low. Kept one medium entry for the config-default case; see refuted appendix for the over-stated "session hijacking" variant.)* Fix: default `CORS_ALLOW_CREDENTIALS=false`; startup error if `*` + credentials.

**python-multipart 0.0.20 pinned with DoS/parameter-smuggling advisories** — medium — `requirements.txt:9`; `src/api/routes.py:511-514` — Real GHSAs (preamble DoS, header DoS, semicolon parameter smuggling) fixed in ≥0.0.30; upload endpoint is unauthenticated by default so preamble DoS is directly reachable. The path-traversal sub-claim is inapplicable (no `UPLOAD_DIR`/disk write). Fix: upgrade to ≥0.0.30.

**Unpinned git dependency `toon-format`** — medium — `requirements.txt:37` — Installed from GitHub HEAD with no SHA; imported at module load in core paths; every image rebuild pulls current HEAD. Upstream compromise/force-push executes with access to `DATABASE_URL`/`REDIS_URL`. Fix: pin to a commit SHA.

**Plaintext infrastructure connections in all deployment configs** — medium — `docker-compose.yml:61-63`; `k8s/base/configmap.yaml:11,25`; `helm/contex/values.yaml:85,108`; `k8s/overlays/prod/kustomization.yaml:17` — `postgresql+asyncpg://` (no `sslmode`), `redis://`, `http://` OpenSearch everywhere including the prod overlay. Defense-in-depth gap; exploitable only after lateral movement. Fix: `sslmode=require`, `rediss://`, `https://` in the prod overlay.

**Ephemeral JWT secret invalidates service-account tokens on restart / across replicas** — medium — `src/core/service_accounts.py:34` — `SERVICE_ACCOUNT_JWT_SECRET` defaults to a fresh `secrets.token_urlsafe(32)` per process; k8s runs `replicas: 2` and doesn't mount this secret, causing 50% JWT rejection with no clear error. Corrected from high to medium: in default config no middleware consumes the JWT. Fix: mount as a required k8s Secret; startup check when service accounts exist.

**API key hashing unsalted; `API_KEY_SALT` is dead code** — medium — `src/core/auth.py:143,180`; `src/core/service_accounts.py:142,327,411`; `main.py:107` — All keys stored as bare `sha256`; the validated `API_KEY_SALT` is never applied. Corrected from high to medium: `secrets.token_urlsafe(32)` keys have ~192–256 bits, so rainbow tables are infeasible — the real defects are a false documented guarantee and weak cross-install isolation. *(Reported by sec-config and onb-coldstart — merged.)* Fix: `hmac.new(salt, key, sha256)` at all sites; ideally migrate to a KDF.

### Low

**OpenSearch TLS certificate verification permanently disabled** — low — `src/core/hybrid_search.py:55-57` — `verify_certs=False` etc. applied unconditionally; MITM risk when `OPENSEARCH_URL` is https. Only reachable when the non-default `HYBRID_SEARCH_ENABLED=true` is set. Fix: only skip verification for `http://` (dev); honor a CA bundle for https.

**Webhook endpoint URL accepts non-HTTP schemes (`file://`, `gopher://`)** — low — `src/api/webhook_routes.py:38`; `src/core/webhooks.py:392` — `url` is plain `str`; exotic schemes are stored but httpx raises `UnsupportedProtocol` at delivery (caught and logged), so no actual SSRF via alternative schemes — a defensive-validation gap, not an exploit. Fix: type as `AnyHttpUrl`.

**Version history/restore routes bypass all auth and tenant checks** — low — `src/api/version_routes.py:15-236` — No auth/RBAC/ownership dependencies; but the routes are effectively dead because they call the non-existent `event_store.get_events()` and 500 before doing anything (see Oversights). Fix: add auth + ownership deps and fix the method call, or remove the module.

**Any MCP client can delete any other client's subscription** — low — `src/core/mcp_adapter.py:51-55`; `src/core/subscriptions.py:45-53` — Unconditional delete by ID; IDs are 128-bit UUIDs so enumeration is impractical and impact is single-subscription DoS. Fix: bind subscriptions to session/principal and enforce on delete.

**Exception-message leakage to MCP clients via `str(e)`** — low — `mcp/shared/jsonrpc_dispatcher.py:757` (SDK); `src/core/subscriptions.py:42` — Stateful sessions serialize unhandled exceptions (e.g., `KeyError(subscription_id)`) to the client, leaking IDs and potentially DB error strings. Moot as a boundary crossing in default config (no access control). Fix: wrap handler bodies, convert to sanitized `MCPError`.

**Path-prefix tenant routing `/t/{tenant_id}/` trusts URL tenant ID** — low (needs confirmation) — `src/core/tenant_middleware.py:196-200` — Same class as the X-Tenant-ID spoof; lower priority method, unverified by skeptic. Fix: validate against the key's tenant or remove the method.

**Unprotected `DELETE /agents/{agent_id}`** — low (needs confirmation) — `src/api/routes.py:747-770` — No ownership check; any caller can unregister any agent. Fix: record project/tenant at registration, verify on delete.

**Server binds 0.0.0.0 but MCP DNS-rebinding protection allows only localhost** — low (needs confirmation) — `main.py:274,449` — `streamable_http_app()` defaults `host=127.0.0.1`, auto-enabling `allowed_hosts=["127.0.0.1:*","localhost:*"]`; remote clients get HTTP 421. Reliability issue for hostname-addressed deployments. Fix: pass the real host or disable rebinding protection behind a proxy.

**Prometheus `/metrics` exposes project/tenant IDs unauthenticated** — low (needs confirmation) — `src/api/routes.py:95-102`; `src/core/metrics.py:27,34,42,313,321` — Label dimensions leak all project/tenant IDs; unauthenticated in default config. (Note: skeptic separately *refuted* the claim that this is specifically broken when `AUTH_ENABLED=true` — with auth on, `/metrics` is not in `public_paths` and does require a key.) Fix: restrict to internal network or strip ID labels.

**`/health` discloses internal infra details** — low (needs confirmation) — `src/api/routes.py:50-66`; `src/core/health.py:60-82,119-124` — Public health endpoint surfaces raw component error strings (possibly internal IPs). Fix: return status-only publicly; gate a detailed endpoint behind auth.

**Filename used as `data_key` without sanitization** — low (needs confirmation) — `src/api/routes.py:541,553` — `../../etc/passwd`-style filenames become DB keys (not filesystem paths — no host traversal), polluting/colliding the store. *(Reported by sec-data and sec-webhook — merged.)* Fix: `Path(filename).name` + strip special chars, or a `pattern=` model constraint.

**`data_needs` list has no count/length bound** — low (needs confirmation) — `src/core/models.py:12-20` — Each need triggers an encode + vector scan; unbounded list is an unauthenticated DoS in default config. Fix: `Field(max_length=50)` + per-string cap; mirror in the MCP subscription handler.

**Webhook delivery log exposes remote response body** — low (needs confirmation) — `src/core/webhooks.py:738`; `src/api/webhook_routes.py:479` — Stores/returns ≤500B of the target's response; becomes an SSRF oracle for internal services. Fix: store only status/content-type once SSRF protection lands.

**DOCX parser follows embedded relationships / no resource cleanup** — low (needs confirmation) — `src/core/node_parsers.py:764-875` — `python-docx` on arbitrary bytes may resolve external relationships and never closes on exception paths. Fix: validate ZIP magic, close in `finally`, sandbox parsing.

**OpenSearch container: passwordless `sudo chown` unscoped** — low (needs confirmation) — `opensearch/Dockerfile:10-11` — `opensearch ALL=(root) NOPASSWD: /bin/chown` with any args aids in-container privesc after RCE. Applies only to the Railway build path (compose uses the stock image). Fix: scope to the data dir.

**Bootstrap admin key printed in plaintext logs** — low (needs confirmation) — `main.py:129-140` — Raw key emitted via `logger.warning()` (forwarded to aggregators) and `print()`. Fix: print once to stderr with a SAVE-THIS notice, or require `BOOTSTRAP_ADMIN_KEY` pre-set.

**`readOnlyRootFilesystem: false` in k8s/Helm** — low (needs confirmation) — `k8s/base/deployment.yaml:208`; `helm/contex/values.yaml:33` — Writable root FS lets an RCE modify `/app` code. Fix: set true + `emptyDir` mounts for cache/tmp.

**python-dotenv 1.0.0 symlink-write advisory** — low (needs confirmation) — `requirements.txt:36` — Not exploitable (no `set_key`/`unset_key` usage), routine hygiene. Fix: upgrade to ≥1.2.2.

---

## Onboarding friction

### High

**QUICKSTART and every example file use bare API paths that 404** — corrected to medium — see Medium.

*(No findings remain at high after skeptic corrections; the three items originally filed high by onb-coldstart/onb-docs were the RBAC and admin-guard bugs and the sandbox bypass — all deduplicated into the Security section above, where they carry high severity. The genuinely onboarding-only high-filed items were all downgraded to medium.)*

### Medium

**Quickstart claims "Postgres + Redis + app" but compose requires OpenSearch (~1.5GB RAM, 1-3 min cold start)** — medium — `README.md:16`; `docker-compose.yml:32-50,70-76` — `contex` hard-depends on `opensearch: service_healthy`; `HYBRID_SEARCH_ENABLED=true` in compose makes it load-bearing; the model also downloads on first request. A newcomer on <4GB free RAM sees an "unhealthy"/hung stack. *(Reported by onb-coldstart and onb-docs — merged.)* Fix: document all four services + startup time + RAM; offer a `docker-compose.min.yml` with hybrid search off; pre-bake the model in the Dockerfile.

**QUICKSTART.md and shipped examples call bare paths that 404** — medium — `QUICKSTART.md:94,111,129,183,293,337,373,395,401,407`; `examples/publish_data.py:23,42,78,119,151`; `examples/agent_redis.py:32` — Routes require `/api/v1` (or legacy `/api`) prefix; bare `/data/publish`, `/agents/register`, etc. return 404 with no redirect. Every quickstart/example call fails. Corrected from high to medium (docs correctness, no security/runtime impact). Fix: prefix all with `/api/v1/`.

**Developer setup omits `pip install` and `contex_test` DB creation; lists non-existent examples** — medium — `README.md:448-464,491-498` — No install step (and `toon-format` is a VCS dep needing git+network); tests target `contex_test` (`conftest.py:22-23`) which compose never creates → `InvalidCatalogNameError`; four referenced example files don't exist. Fix: add `pip install -r requirements.txt` + `CREATE DATABASE contex_test`; correct the examples list.

**`RATE_LIMIT_ENABLED` documented as independent but ignored unless `AUTH_ENABLED=true`** — medium — `README.md:293-296`; `main.py:317-332`; `src/core/config.py:48,136` — `RateLimitMiddleware` is only added inside `if AUTH_ENABLED`; `RATE_LIMIT_ENABLED` is read only by dead `config.py`. Operators get a silent no-op and a false sense of protection. Fix: remove the standalone example or wire an actual conditional.

**Rate-limiter path patterns don't match actual routes (docs claim they work)** — medium — `src/core/rate_limiter.py:163-168`; `RATE_LIMITING.md`, `SECURITY.md` — Patterns `/api/publish` etc. never match `/api/v1/*`; everything gets the flat 60/min. Docs assert differentiated limits. *(This is the onboarding/docs framing of the security rate-limit finding above — deduplicated; fix once.)*

### Low

**README documents non-existent endpoints (`/api/v1/query`, `/projects/{id}/export`, `/api/docs`)** — low — `README.md:329,331,386`; `main.py:221` — Query needs a `project_id` segment; `/export` was replaced by `/data?include_events=true`; Swagger is at `/docs` not `/api/docs`. `/api/health` and the missing-`/v1/` events example actually resolve via the legacy `/api` mount (skeptic refuted those two sub-claims). *(Reported by onb-coldstart and onb-docs — merged.)* Fix: correct the three real errors.

**Tracing env vars wrong in both `config.py` and `.env.example`** — low — `src/core/config.py:146-147`; `.env.example:111-114`; `src/core/tracing.py:243-249` — Three disjoint var-name sets; only `TRACING_CONSOLE_EXPORT`/`TRACING_OTLP_EXPORT`/`TRACING_OTLP_ENDPOINT` work. Tracing is off by default so no one is silently broken unless they try to enable it. Fix: document the real vars; drop the inert `OTEL_*`/`TRACING_ENABLED` sets.

**`SECURITY.md`/`RATE_LIMITING.md` claim Redis rate limiting; code uses PostgreSQL** — low — `docs/SECURITY.md:79,117`; `docs/RATE_LIMITING.md:7`; `src/core/rate_limiter.py:1,64` — Also wrongly says API keys are checked in Redis (they're in PG). Fix: correct the backend references.

**Missing referenced doc files (`docs/ARCHITECTURE.md`, `README_DOCKER.md`, `docs/assets/demo.gif`)** — low — `README.md:50,444`; `QUICKSTART.md:448`; `docs/demo/capture.md` — Broken links + broken hero image in the README. *(Reported by onb-coldstart and onb-docs — merged.)* Fix: create/stub the files or remove the references.

**`.env.example` documents env vars the app never reads (`DEGRADATION_*`, `DATA_VERSIONING_*`, `OTEL_*`)** — low — `.env.example:75-79,111-114,207-210` — Zero code consumption; `main.py:180` confirms data versioning was removed. The `OTEL_*` mismatch silently breaks observability if used. Fix: remove stale blocks; document the real tracing vars.

**`config.py` reads `REDIS_TIMEOUT` but connection code reads `REDIS_SOCKET_TIMEOUT`** — low — `src/core/config.py:132`; `src/core/pubsub.py:66,105`; `src/core/redis_connection.py:64,116`; `.env.example:25` — Symptom of the dead-`config.py` problem; `.env.example` has the correct name, so operators following it are fine. Fix: align or drop the var (part of the `config.py` cleanup).

**Multiple app-read env vars absent from `.env.example`** — low (needs confirmation) — `.env.example`; `config.py`/`tracing.py`/`retention.py`/`redis_connection.py` — Missing `VECTOR_STORE`, `REDIS_MODE` + Sentinel vars, `RETENTION_*`, thresholds, etc. Operators must read source to configure HA/retention. Fix: document them all.

**Default bind-host mismatch: `main.py` `0.0.0.0` vs `.env.example` `::`** — low (needs confirmation) — `main.py:449`; `.env.example:4` — Bare-Python IPv6 clients get connection refused; the compose IPv6 healthcheck fallback fails. Fix: align the defaults.

**Additional doc/SDK inaccuracies** — low (needs confirmation), merged group:
- `CONTRIBUTING.md:230` — wrong import path `from src.context_engine import ContextEngine` (should be `from src.core import ContextEngine`).
- `README.md:272-275` — `client.assign_role()` doesn't exist in the SDK.
- Inconsistent repo URLs (`github.com/contex/contex` doesn't exist) across `QUICKSTART.md:16,58`, `README.md:452,505`, `CONTRIBUTING.md:37`; real remote is `cahoots-org/contex`.
- `README.md:490-494` — examples list names files that don't exist.
- `docs/METRICS.md:11,22,400` + `main.py:223` + `README.md:411` — metrics path documented three different ways; real path is `/api/v1/metrics` (bare `/metrics` 404s → Prometheus collects nothing).

Fix: a single documentation-accuracy pass correcting import paths, SDK method references, repo URLs, example lists, and the metrics endpoint path.

---

## Oversights

### High

**Migration 001 broken under asyncpg — entire Alembic chain unusable on a fresh DB** — high — `alembic/versions/001_initial_schema.py:154-159,164` — Creates `embedding` as `LargeBinary` then runs `ALTER ... TYPE vector(384) USING ...::vector` plus HNSW `CREATE INDEX` inside Alembic's transaction; both fail under asyncpg, so `alembic upgrade head` never runs. The project's own plan doc acknowledges this. Every deploy relies on `create_all`, so there is no rollback/incremental-migration path. Corrected from critical to high (app still boots via `create_all`; no runtime/security impact). Fix: create the column directly as `Vector(384)`; issue autocommit DDL for the HNSW index.

**HNSW vector index missing from ORM model — `create_all` never creates it** — high — `src/core/db_models.py:271-276` vs `alembic/versions/001_initial_schema.py:164` — The HNSW index exists only in the (never-run) migration; `Embedding.__table_args__` omits it, so every default deployment and every test runs semantic search as a sequential scan. The product's core feature silently degrades at scale with no error. Fix: add `Index(..., postgresql_using='hnsw', postgresql_ops={'embedding':'vector_cosine_ops'}, postgresql_with={'m':16,'ef_construction':64})`, or issue the `CREATE INDEX` in lifespan startup after `create_all`.

**Event sequence generation is SELECT MAX + INSERT with no locking — concurrent publishes 500** — high — `src/core/event_store.py:54-83`; `src/core/db_models.py:211` — Under READ COMMITTED two concurrent publishes to the same project compute the same `sequence`, and the second hits the unique constraint → `IntegrityError` → 500, with no retry. The losing write is lost. Unauthenticated concurrency triggers it. *(Reported by ovs-schema and ovs-correctness — merged.)* Fix: use a Postgres sequence, `ON CONFLICT` retry, or `SELECT ... FOR UPDATE` on a per-project counter; or order by `BIGSERIAL id`.

### Medium

**Concurrent requests corrupt shared `SemanticDataMatcher` state** — medium — `src/core/context_engine.py:562-582`; `src/core/matcher.py:40-48`; `src/web/routes.py:64-65,159` — Save/mutate/restore of `max_matches`/`threshold` on a singleton around `await` points lets concurrent queries read each other's values; results are silently mis-thresholded/mis-counted. *(Filed as high under security by sec-mcp and as high by ovs-correctness; skeptic corrected the correctness filing to medium — reliability, not security. Deduplicated here.)* Fix: pass `top_k`/`threshold` as arguments through to `match_agent_needs`; eliminate shared mutable state.

**`version_routes.py` calls non-existent `EventStore.get_events()`** — medium — `src/api/version_routes.py:37,101` — Method doesn't exist (`get_events_since`/`get_all_events`/`get_events_by_type` do); all three versioning endpoints 500. Availability-only; no security dimension. Fix: call `get_all_events` (or `get_events_since`).

**`/sandbox/projects/{id}/stats` crashes with AttributeError (dead RediSearch attrs)** — medium — `src/web/routes.py:182-183` — Accesses `semantic_matcher.KEY_PREFIX` and `.redis`, neither of which exists post-Postgres migration; the stats page always 500s. *(Reported by onb-docs and ovs-correctness — merged.)* Fix: rewrite against the `embeddings` table like the adjacent `/data` handler.

**`create_all` bypass: no `alembic_version` row ever written** — medium — `main.py:63-75`; `tests/conftest.py:80-83` — Schema is built via `create_all` with no `alembic stamp head`, so Alembic tracking is permanently out of sync and no incremental upgrade/rollback path exists. Fix: `command.stamp(cfg, 'head')` after `create_all`, or run `alembic upgrade head` exclusively (after fixing 001).

**Two tests have zero assertions (notification + project isolation unverified)** — medium — `tests/test_context_engine.py:276-299,339-354` — `test_publish_notifies_affected_agents` and `test_project_isolation` end on comments with no `assert`; both pass trivially, giving false confidence over pub/sub delivery and tenant isolation. Fix: assert message receipt and assert cross-project non-match.

**Two parallel hybrid-search implementations with inconsistent ownership** — medium — `src/core/hybrid_search.py` (OpenSearch `RankFusionSearch`) vs `src/core/hybrid_search_service.py` (Postgres `HybridSearchService`) — `HYBRID_SEARCH_ENABLED=true` activates the OpenSearch one; the Postgres-native "replacement" is only used in the eval harness, and `matcher.py:4`'s docstring wrongly claims the opposite. Operator hazard: enabling hybrid search silently requires OpenSearch. Fix: pick a canonical impl, wire it in, delete/document the other, correct the docstring.

### Low

**`AuditEvent.event_id` has no `server_default` in the `create_all` path** — low — `src/core/db_models.py:284-285` vs `alembic/versions/001_initial_schema.py:169` — Column is `UUID NOT NULL` with only a Python-side default under `create_all`; a raw SQL insert omitting `event_id` would fail. All current code paths populate it in Python, so latent only. Fix: add `server_default=text('gen_random_uuid()')`.

**Broken top-level `benchmark.py` (pre-reposition Redis-vector API)** — low — `benchmark.py:84,89,93,206-229,251` — Wrong `SemanticDataMatcher(redis=...)` signature, non-existent `embedding_cache`, Redis-hash storage; crashes on run. Not imported by the app. Fix: delete or rewrite against pgvector; the `benchmark/eval/` harness is the real replacement.

**Dead code modules (delete):** low, grouped — each never imported by production or tests:
- `src/core/redis_connection.py:1-264` — duplicates `pubsub.py`'s connection logic.
- `src/core/snapshots.py:1-338` — Redis-backed, orphaned; the parallel Postgres `Snapshot` model/table (`db_models.py:217`) is also never written.
- `src/core/embedding_cache.py:1-237` — `SemanticDataMatcher` no longer uses it; three `embedding_cache_*` Prometheus metrics report permanent zero.
- `src/core/data_normalizer.py:1-189` + `src/core/parsers/` (9 files, ~1,500 lines) — superseded by `node_parsers.py`/`node_converter.py`. (Deleting this moots the XML billion-laughs finding.)
- `src/core/retry_policy.py:1-340` — no callers; `webhook_dispatcher.py` rolls its own retry.
- Plan-1 search stack (`hybrid_search_service.py`, `lexical_search.py`, `vector_search.py`, `rank_fusion.py`) — production-dormant, only used by the eval harness.

Fix: delete these modules and their stale Prometheus metric definitions; correct any docstrings that reference them.

**Minor oversights (needs confirmation — unverified):** low, grouped:
- `src/api/routes.py:9-10` — duplicate `QueryResponse` import.
- `src/core/context_engine.py` et al. — stale `except NotImplementedError` TOON fallback branches now unreachable (`toon.encode()` works).
- `src/core/metrics.py:157-169,507-514` — `update_redis_connections`/`update_registered_agents_count` never called; gauges stuck at 0.
- `src/core/config.py:208-225` — `load_and_validate_config()`/`ContexConfig` never called by `main.py`; all validation (CORS, salt, RRF) skipped. This is the root cause of the config-var-mismatch onboarding findings.
- `src/core/__init__.py:11,26` — `AgentContext` exported inconsistently and unused; `SemanticSearchRequest` (`models.py:121-130`) and `get_all_circuit_breakers()` (`circuit_breaker.py:309-315`) defined but unused.
- `src/core/db_models.py:438`; `alembic/versions/003_add_subscriptions.py:26` — `Subscription.tenant_id` lacks the FK constraint every other tenant-linked table has; orphaned subscriptions possible under multi-tenant.
- `src/web/live.py:39-47` — SSE stream dies permanently on any transient Redis error; no retry/heartbeat.
- `src/core/subscriptions.py:65-93` — `reconcile_project` TOCTOU window can emit spurious `subscription:{id}:updated` events (no data corruption).
- 37 `print()` statements in production paths (`context_engine.py`, `hybrid_search.py`, `rbac_middleware.py:160`) bypass structured logging and `LOG_LEVEL`.

Fix: address opportunistically alongside the dead-code and config-wiring cleanups.

---

## Appendix — Considered but refuted

- **CORS "session-hijacking via any web page"** (`main.py:278-293`) — Browsers reject wildcard-origin + credentials; the app authenticates via headers, not cookies; auth is off by default. The wildcard+credentials *config* is kept as a low/medium hygiene finding, but the session-hijacking exploit does not work.
- **Batch endpoints "do not enforce per-event tenant ownership"** (`routes.py:1278-1361,1364-1451`) — True but not batch-specific: the single-item publish path has the identical omission, and it's neutralized in default config (single tenant). Tracked as the known Layer-1b authorization gap, not a distinct batch finding.
- **`/mcp` sub-app "bypasses all auth middleware even when `AUTH_ENABLED=true`"** (`main.py:274-275`) — Factually wrong for Starlette 1.6.0: middleware wraps the router (including mounts), and `/mcp` is not in `public_paths`, so with auth on an unauthenticated `/mcp` request gets 401. The real exposure is only the default `AUTH_ENABLED=false`, captured in the critical `/mcp` finding.
- **`/metrics` "unauthenticated even when `AUTH_ENABLED=true`"** (`routes.py:95-102`) — Wrong for the auth-on path: `/metrics` isn't whitelisted, so it requires a key when auth is enabled. Only the global auth-off default applies, which isn't metrics-specific. (The ID-label disclosure in default config is kept as a separate low finding.)
- **Jinja2 3.1.2 sandbox-escape / xmlattr CVEs** (`requirements.txt:7`) — No exploitable surface: templates are fixed on-disk files rendered by name, no user-controlled template source/name, and `xmlattr` is unused. Routine-upgrade hygiene only.
- **Second "MCP mount bypasses middleware" report** — Same architectural error as above; refuted for the same Starlette-lazy-stack reason.