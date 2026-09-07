# Tenant Foundation — "Every Row Always Has a Tenant" (PR A)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Make tenant ownership a total invariant — every tenant-scoped row always carries a tenant, defaulting to `DEFAULT_TENANT_ID` ("default") when none is assigned — so that enabling multi-tenancy later never orphans pre-existing data. This is the data-model foundation the ownership *enforcement* (PR B) sits on.

**Architecture:** A backfill+constraint migration (007) assigns all currently-null/unmapped tenant data to the permanent default tenant and makes the columns `NOT NULL` with `server_default='default'`. Write paths stop emitting explicit NULL. The column is *always populated regardless of the `MULTI_TENANT_ENABLED` flag*; the flag only governs whether requests are filtered/enforced (that's PR B). Contex already provides `DEFAULT_TENANT_ID` + `ensure_default_tenant()` (`src/core/tenant.py:748-776`).

**Tech Stack:** Alembic (sync migrations), SQLAlchemy async ORM, Postgres+pgvector, pytest (live `pgvector/pgvector:pg16` + Redis).

**Basis:** Research on single→multi upgrade (industry norm: NOT NULL + default tenant + backfill; never null). Reverses the earlier keep-nullable decision. Ledger: `../tier0-ownership/.superpowers/sdd/2026-09-07-tier0-ownership-batch1/progress.md` (RE-PLAN section).

## Global Constraints

- **Alembic head is `006`; this adds `007`** (down_revision `006`). Keep `db_models.py` ORM in agreement (it's for query-building, not the schema source).
- **Scope (locked):** columns `subscriptions.tenant_id`, `api_keys.tenant_id`, `service_accounts.tenant_id`, `events.tenant_id`; plus `tenant_projects` row backfill. **Explicitly OUT (separate follow-up):** `audit_logs`, `webhook_endpoints`, `agent_registrations`, `APIKeyRole.tenant_id`.
- **The migration must be idempotent-safe and fresh-DB-safe:** it runs both on existing prod data AND against the empty CI container DB (conftest migrates from empty every run). Ensure the `default` tenant row exists (via the migration itself) *before* backfilling/adding FKs that reference it.
- **NOT NULL non-blocking recipe:** backfill → `server_default` → add `NOT NULL` (Postgres `SET NOT NULL`; the tables are small on self-host, a plain `SET NOT NULL` is acceptable — note batching only if events is large).
- **Decouple from the flag:** population happens always; do NOT gate any of this on `MULTI_TENANT_ENABLED`.
- **Commits:** single-purpose, each CI-green. Branch `worktree-tenant-foundation`; do not push/PR until asked.
- **Style (Rob):** top-level imports, no meta comments, concise. Tests against live PG: `export DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test"`.

## File Structure
- **New:** `alembic/versions/007_tenant_not_null_backfill.py`; `tests/test_migration_tenant_backfill.py`.
- **Modify:** `src/core/db_models.py` (flip the four `tenant_id` columns to non-null + server_default; add subscriptions FK; ondelete changes), `src/core/subscriptions.py` (create() never writes explicit NULL), `src/api/tenant_routes.py` (the `TenantManager(redis)`→`(db)` accessor fix).

---

## Task 1: Migration 007 — backfill + constraints

**Files:** Create `alembic/versions/007_tenant_not_null_backfill.py`. Read `006_event_sequence_counter.py` first to match this repo's alembic idioms (revision vars, op style, async/sync).

**Interfaces — Produces:** alembic revision `007`, down_revision `006`.

- [ ] **Step 1: write the migration** (upgrade). Order matters:
  1. Ensure the default tenant row exists: `INSERT INTO tenants (tenant_id, ...) VALUES ('default', ...) ON CONFLICT (tenant_id) DO NOTHING` — match the columns `ensure_default_tenant()` uses (read `tenant.py:751-776`; replicate its non-null column values, or call a minimal insert with the same required fields). This must work on a fresh empty DB.
  2. Backfill nulls: for each of `subscriptions`, `api_keys`, `service_accounts`, `events` → `UPDATE <t> SET tenant_id='default' WHERE tenant_id IS NULL`.
  3. Backfill `tenant_projects`: insert a `(tenant_id='default', project_id)` row for every distinct `project_id` present in `events` ∪ `embeddings` ∪ `subscriptions` that has no existing `tenant_projects` row. Use `INSERT ... SELECT DISTINCT ... WHERE NOT EXISTS (...) ON CONFLICT DO NOTHING`.
  4. `server_default`: `op.alter_column(<t>, 'tenant_id', server_default='default')` for the four tables.
  5. `NOT NULL`: `op.alter_column(<t>, 'tenant_id', nullable=False)` for the four tables (after backfill).
  6. FK: add `subscriptions.tenant_id → tenants.tenant_id` (name it e.g. `fk_subscriptions_tenant`), `ondelete='RESTRICT'`.
  7. ondelete change: drop+recreate the `api_keys` and `service_accounts` tenant FKs with `ondelete='RESTRICT'` (were `SET NULL`).
  `downgrade()`: reverse (drop FKs added, re-add nullable, drop server_default, revert ondelete). Downgrade need not un-backfill data.

- [ ] **Step 2: run it forward on the CI DB** — the conftest session fixture already runs migrations to head against the empty container DB. Run `python -m pytest tests/test_migration_fresh_db.py -v` (existing fresh-DB gate) → must still pass to head `007`.

- [ ] **Step 3: ORM agreement** — in `src/core/db_models.py`, change the four `tenant_id` columns from `Mapped[Optional[str]] ... nullable=True` to non-null with `server_default="default"`; add the `subscriptions.tenant_id` FK to `tenants` (it currently has none); change `api_keys`/`service_accounts` FK `ondelete` to `"RESTRICT"`. (Events keeps `ondelete="CASCADE"`.)

- [ ] **Step 4: commit** `feat(db): migration 007 — backfill tenant_id to default + NOT NULL (never-orphan)`.

---

## Task 2: Write paths never emit NULL tenant

**Files:** `src/core/subscriptions.py`; Test: `tests/test_migration_tenant_backfill.py` (or a subscriptions test).

`create()` currently signs `tenant_id=None` and writes it explicitly (`subscriptions.py:23,29`) — an explicit NULL defeats `server_default`. Fix so a create with no tenant lands as `'default'`, not NULL.

- [ ] **Step 1: failing test** — with the 007 schema, `SubscriptionService.create(project_id, needs)` (no tenant_id) produces a row whose `tenant_id == 'default'` (not NULL, no IntegrityError).
- [ ] **Step 2: implement** — in `create()`, when `tenant_id is None` resolve it: prefer `await tenant_mgr.get_project_tenant(project_id)`, else `DEFAULT_TENANT_ID`; pass that (never a bare `None`) into the `Subscription(...)`. (Import `DEFAULT_TENANT_ID` top-level.) Keep the signature `tenant_id=None` for callers, but normalize before insert. If wiring a `tenant_mgr` into the service is heavy, at minimum default `None`→`DEFAULT_TENANT_ID` so NOT NULL never trips; note the project-derivation option for PR B.
- [ ] **Step 3: run** the new test + `python -m pytest tests/test_subscription_service.py tests/test_subscription_consistency.py -v`.
- [ ] **Step 4: commit** `fix(subscriptions): default tenant on create so NOT NULL never trips`.

---

## Task 3: Fix the `tenant_routes` TenantManager accessor bug

**Files:** `src/api/tenant_routes.py`.

`get_tenant_manager` (line ~95-99) constructs `TenantManager(request.app.state.redis)`, but the constructor takes the DB (`TenantManager(db)`, `tenant.py:764`). Pre-existing bug surfaced during Batch 1.

- [ ] **Step 1:** change it to `TenantManager(request.app.state.db)`. Confirm `TenantManager.__init__` signature in `tenant.py`. If tenant-management tests exercised the manager via redis, verify they still pass; if a test encoded the wrong arg, fix the test to the correct DB arg.
- [ ] **Step 2: run** `python -m pytest tests/test_tenant.py -v`.
- [ ] **Step 3: commit** `fix(tenant): construct TenantManager with the DB, not redis`.

---

## Task 4: Foundation tests — invariant holds

**Files:** `tests/test_migration_tenant_backfill.py`.

- [ ] Prove, against the live DB migrated to `007`:
  1. Inserting a `Subscription`/`APIKey`/`ServiceAccount`/`Event` without a tenant_id lands `'default'` (server_default), not NULL.
  2. Attempting to write an explicit `tenant_id=NULL` raises IntegrityError (NOT NULL enforced).
  3. `SubscriptionService.create()` with no tenant → row tenant_id `'default'`.
  4. A pre-seeded pre-migration-style NULL row would be `'default'` after backfill — simulate by asserting the backfill UPDATE logic (or seed via raw SQL bypassing the default, then run the backfill statement) — keep this focused; the fresh-DB gate covers migrate-to-head.
- [ ] Commit `test(db): tenant-always-set invariant + NOT NULL enforcement`.

---

## Self-Review
- Never-orphan: migration backfills existing nulls + unmapped projects to `default` (Task 1 §2-3); columns NOT NULL + server_default (Task 1 §4-5); write path can't emit NULL (Task 2). Flip-on is now a non-event for ownership.
- Fresh-DB safe: default tenant ensured inside the migration before FK/backfill; existing fresh-DB gate re-run (Task 1 §2).
- Scope: four core tables + tenant_projects; auxiliary tables explicitly deferred (Global Constraints) — not silently dropped.
- ORM/schema agreement kept (Task 1 §3). FK/ondelete: subscriptions FK added; api_keys/service_accounts → RESTRICT; events stays CASCADE (deliberate).
- Placeholder check: the one soft spot is replicating `ensure_default_tenant`'s required columns in raw SQL — Task 1 §1 instructs reading `tenant.py:751-776` to match them; if the tenants table's non-null columns are unclear, that task should NEEDS_CONTEXT rather than guess.
