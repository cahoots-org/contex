# Design: Sandbox Redesign + Authenticated, Attributed Context Bus

- **Date:** 2026-08-21
- **Status:** Draft for review
- **Author:** Rob Miller (with Claude)

## Context

What began as "the sandbox looks bad and has too many controls" surfaced, through
design discussion, a set of deeper gaps in how Contex authenticates and attributes
data flowing through the bus. Rather than restyle a UI that sits on top of an
identity-blind pipeline, this spec covers the whole stack in three layers, from the
security-relevant core outward to the UI.

The driving product framing: Contex is an **MCP-native, real-time semantic context
bus**. Agents describe a need; Contex continuously routes the right project data to
them over hybrid (BM25 + vector) matching. The sandbox's job is to make a developer
*trust the routing* — which means making the routing decision, its provenance, and
its live behavior legible.

### Findings from the current code (the "why")

- **Publish is identity-blind end to end.** `contex_publish` (MCP,
  `src/core/mcp_adapter.py:64`) and the HTTP publish route both call
  `ContextEngine.publish_data(DataPublishEvent(...))` (`src/core/context_engine.py:219`).
  `DataPublishEvent` carries no tenant/actor/source. `EventStore.append_event`
  (`src/core/event_store.py:29`) *does* accept `tenant_id`, but nothing upstream fills
  it, and no handler checks the caller's scope against the `project_id` being written.
  When `MULTI_TENANT_ENABLED` is on, that is a tenant-isolation gap on both transports.
- **Subscribe is one step ahead but has the same entry-point gap.**
  `SubscriptionService.create(project_id, needs, tenant_id=None, scope=None, ...)`
  (`src/core/subscriptions.py:22`) already accepts `tenant_id`/`scope`, and the
  `Subscription` row stores `tenant_id`. But `contex_create_subscription`
  (`src/core/mcp_adapter.py:43`) and the web `/sandbox/subscribe` route don't populate
  them. Subscribe is a *continuous read* of a project's context — an unauthenticated or
  cross-tenant subscription is a live data leak, so this is higher-stakes than publish.
- **The MCP transport authenticates at the perimeter but drops the principal.**
  `/mcp` is mounted as a sub-app (`main.py:275`) behind the parent middleware, and is
  **not** in `AuthMiddleware.public_paths` (`src/core/auth.py:77`), so when auth is
  enabled an unauthenticated `/mcp` call is rejected. But the principal set in
  `request.state` is never threaded into the MCP tool handlers, so they can neither
  attribute an actor nor enforce per-call project/tenant scope. MCP is a long-lived
  *session* transport, so the fix is to bind the principal to the session and thread it
  into every tool invocation.
- **Header mismatch on the MCP path.** MCP clients send `Authorization: Bearer <token>`;
  `AuthMiddleware` only reads `X-API-Key` (`src/core/auth.py:93`). Even once the
  principal is threaded, an MCP client cannot authenticate today.
- **Credential scoping is incomplete.** `create_api_key` (`src/core/auth.py:156`) mints
  prefixed (`ck_`), hashed, revocable keys with generic `scopes`. Per-project scoping
  (`allowed_projects`) exists only on service accounts. There is no expiry/rotation and
  no read-vs-write distinction.
- **Web read paths already had dead Redis-era code** (fixed in a prior session):
  project listing and project-data now read from the `embeddings` table. See
  `stale-redis-search-paths` memory.

## Goals

1. Every publish and subscribe is **authenticated, tenant-scoped, and attributed** on
   both HTTP and MCP transports.
2. Events carry server-attested **`source`** (how) and **`actor`** (who) provenance.
3. A redesigned sandbox whose primary interaction is **search → auto-watch**, backed by
   a live, cross-project, access-scoped **event firehose**, plus a **Publish tab** that
   exercises the real MCP publish tool.
4. Restyle to a calm, dev-infra aesthetic (monochrome + one accent, mono for data),
   with relevance-tuning knobs behind an **Advanced** disclosure.

## Non-goals

- Full OAuth 2.1 authorization-server behavior for MCP (accept bearer tokens now; leave a
  clean path). 
- Source connectors (git, Drive, Confluence, issue trackers), streaming/CDC ingestion,
  and document ingestion — these are roadmap; the design must *accommodate* them (event
  `source` is an open enum) but does not build them.
- A dashboard/CLI credential-issuance UX (bootstrap + admin endpoints remain the way in).
- Dark mode.

## Layer 1 — Authenticated, tenant-scoped, attributed publish + subscribe (security core)

This is the spine. Both provenance and authorization need the same thing: thread
`(tenant, actor, source)` from each authenticated entry point through to the write, and
enforce scope at that boundary.

### Credential model

- Extend the API-key model so scoping lives on the credential, not only on service
  accounts:
  - `allowed_projects` (empty = all, matching the service-account convention).
  - `scopes` express **read** (subscribe/query) vs **write** (publish), per project.
- Add optional **expiry** and a rotation path (overlapping validity) — at minimum for
  service accounts.
- Keep `ck_` API keys as the primitive and issuance via bootstrap/admin endpoints.

### Principal resolution and enforcement

- **HTTP:** fill `(tenant, actor)` from `request.state` (already populated by
  `AuthMiddleware`/`TenantMiddleware`). `actor` mirrors the audit model:
  `{actor_id, actor_type ∈ {api_key, service_account, agent}, actor_ip}`
  (see `src/api/audit_routes.py:99`).
- **MCP:** 
  - Accept `Authorization: Bearer <ck_...>` in `AuthMiddleware` (in addition to
    `X-API-Key`).
  - Bind the resolved principal to the MCP **session** and thread it into tool handlers
    (`contex_publish`, `contex_create_subscription`, `contex_query`,
    `contex_delete_subscription`).
- **Enforcement (both transports):** before writing/reading, check the principal's
  `allowed_projects`/`scopes` against the call's `project_id` and operation. Reject on
  mismatch. `source` is stamped server-side by the entry point (`mcp` / `api` / later
  `document`/`webhook`/`connector`) and is never client-supplied.

### Plumbing changes

- Extend `DataPublishEvent`, `ContextEngine.publish_data`, and `EventStore.append_event`
  to carry `(tenant_id, actor, source)`.
- Populate `SubscriptionService.create`'s existing `tenant_id`/`scope` from the entry
  points, and enforce read scope on subscribe and on the SSE stream.

### Attribution is server-attested

Neither `source` nor `actor` is ever read from the request body — both are derived from
the authenticated context, exactly as the audit trail already does. This is what makes
the event log trustworthy.

## Layer 2 — Event store read side + firehose

- **Schema:** add `source` and `actor` (`actor_id`, `actor_type`, `actor_ip`) columns to
  the `events` table (`src/core/db_models.py:186`). Alembic migration; default
  `source = 'api'` and null actor to backfill existing rows.
- **Two read paths:**
  - Per-project catch-up by `sequence` (exists — agent catch-up).
  - **Global firehose** ordered by the table's global `id` (BigInteger autoincrement),
    since `sequence` is per-project (`idx_events_project_sequence` is unique on
    `project_id + sequence`). Tail by `id > last_seen_id`.
- **Endpoint:** a live SSE feed over the firehose, **access-scoped** to the caller's
  `allowed_projects` (all when auth disabled — dev "no password" behavior, matching
  RedisInsight/pgAdmin conventions). Windowed (last N + live tail), not load-all.
- **Filters:** `project`, `source`, `actor` (multi-select). Default view = everything the
  caller can see (Kafka-UI overview model), narrowed by filters.

## Layer 3 — Sandbox redesign

### Interaction model

- **Search → auto-watch.** Executing a query is what promotes it to a live subscription
  on that need. Results appear immediately (one-shot), then keep updating. No separate
  "Watch" mode/button — search and watch are one gesture.
- **Saved recent queries** for one-click reload.
- **Publish tab.** A first-party console that exercises the real `contex_publish` **MCP**
  tool (transport radio: MCP default, HTTP alternate — proving parity). Shows the returned
  sequence and the equivalent programmatic call (MCP tool-call JSON / `curl`) so it teaches
  the real integration rather than being demo-only product surface. Not a primary
  ingestion route.
- **Persistent event firehose panel**, visible across both tabs (it's the always-on
  observability surface; tabs are the two things you *do*). Live SSE; events from any
  client — including agents — appear in real time, tagged with `source` and `actor`.
- **Reveal outputs, hide inputs.** Results show a **match score** (and, if feasible, which
  signal — lexical vs vector — drove it). Relevance-tuning knobs (post-filter threshold,
  keyword/BM25 threshold, vector threshold, top-k, max tokens) move into an **Advanced**
  disclosure. Hiding them is on-message: the product routes well *by default*.

### Layout / structure

- `Search` and `Publish` tabs; project selector governs **Search only** (per-project).
- The firehose is independent and cross-project with its own project/source/actor filters.
- Fix the fragile Alpine `x-data` blob: extract the component into a
  `function sandboxApp(){ return {...} }` in `{% block scripts %}`, reducing markup to
  `x-data="sandboxApp()"`. (Prior session patched inline double-quotes with `&quot;`; this
  removes the whole class of bug.)

### Visual direction

- Refined & minimal, dev-infra idiom: near-monochrome neutral palette, one restrained
  accent (radio-group default; monochrome-with-one-accent leaning), the search box as the
  visual anchor, hairline borders over filled boxes, and **monospace for keys/JSON/IDs**.
- Replace the inline-styled multicolor icon buttons in the data panel with quiet
  monochrome controls.

## Data model summary

| Table / type | Change |
|---|---|
| `events` | + `source` (string, default `api`), `actor_id`, `actor_type`, `actor_ip` (nullable) |
| `DataPublishEvent` | + `tenant_id`, `actor`, `source` |
| `ContextEngine.publish_data` / `EventStore.append_event` | accept & persist `(tenant, actor, source)` |
| API key model | + per-project `allowed_projects`, read/write `scopes`, optional expiry/rotation |
| `Subscription` entry points | populate existing `tenant_id`/`scope`; enforce read scope |

## Security considerations

- **Object-level authorization** is the central security fix: perimeter auth proves "a
  valid key," not "may act on *this* project." Enforcement happens where `project_id` is
  known — the handler — against the credential's `allowed_projects`/`scopes`.
- **Subscribe is a read leak vector** — enforce read scope on create *and* on the SSE
  stream, not just at creation.
- **Server-attested provenance** — `source`/`actor` derived from authenticated context,
  never the body.
- **Auth-disabled dev mode** intentionally shows/serves everything (local-Redis-no-password
  convention). The auth-*enabled* path must be first-class, not an afterthought.
- **MCP bearer** — without accepting `Authorization: Bearer`, real MCP clients cannot
  authenticate regardless of the rest.

## Open questions / follow-ups

1. Expiry/rotation: scope to service accounts first, or all API keys?
2. `actor_type` granularity: is a registered `agent_id` bound to a credential at
   registration (so agent identity is attested), or only the credential principal?
3. Match-signal transparency: is lexical-vs-vector attribution cheaply available from the
   hybrid matcher, or is score-only the pragmatic v1?
4. Firehose volume controls (window size, retention/pagination) — defaults?

## Rough sequencing

1. **Layer 1** — credential scoping + MCP bearer + session principal + enforce + thread
   `(tenant, actor, source)`. Security core; everything else sits on it.
2. **Layer 2** — events columns + migration + firehose endpoint (scoped).
3. **Layer 3** — sandbox interaction, Publish tab, firehose panel, restyle, Alpine refactor.

Layers 1 and 2 are backend and independently testable; Layer 3 is the UI on top.
