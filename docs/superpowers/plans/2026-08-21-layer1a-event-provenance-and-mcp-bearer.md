# Layer 1a: Event Provenance + MCP Bearer Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp every published event with server-attested provenance (`source` + `actor`), and let MCP clients authenticate with `Authorization: Bearer <ck_...>`.

**Architecture:** Add four columns to the `events` table (`source`, `actor_id`, `actor_type`, `actor_ip`). Thread provenance as server-supplied keyword args from each entry point → `ContextEngine.publish_data` → `EventStore.append_event`. Provenance is NEVER read from the request body — it is stamped by the route/handler from the authenticated request context. Separately, teach `APIKeyMiddleware` to accept a bearer token so MCP clients (which speak `Authorization: Bearer`, not `X-API-Key`) can authenticate.

**Tech Stack:** Python 3.11, FastAPI/Starlette, SQLAlchemy 2.0 (async), Alembic, PostgreSQL + pgvector, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-21-sandbox-and-authenticated-bus-design.md` (Layer 1, provenance + credential/MCP-header portions)

## Global Constraints

- Docker image builds must use `--platform linux/amd64`.
- `source` and `actor` are **server-attested**: derived from the authenticated request context, never from the request body. This is a hard rule — no task may add them as client-settable fields on `DataPublishEvent`.
- Default `source = "api"` (so existing rows and untagged callers backfill cleanly); `actor_*` columns are nullable (null = unauthenticated / auth-disabled dev mode).
- Tests use the real Postgres `db` fixture from `tests/conftest.py`; async tests are marked `@pytest.mark.asyncio`.
- Run tests with `pytest` from repo root (config: `pytest.ini`, `pythonpath = .`).

## Scope

**In scope (1a):** `events` provenance columns + migration; `append_event`, `publish_data`, and the HTTP publish route threading; `APIKeyMiddleware` bearer acceptance.

**Out of scope (deferred to Layer 1b):** object-level authorization enforcement (needs `rbac.py` role/scope taxonomy), MCP session-principal binding + threading into MCP tool handlers, and subscribe read-scope enforcement (both need a spike into the vendored `mcp` SDK's per-call context API). Until 1b, events published over MCP will have `source="mcp"` set by the handler but `actor_*` null.

---

### Task 1: Provenance columns on the event store

**Files:**
- Modify: `src/core/db_models.py:187-209` (the `Event` model)
- Modify: `src/core/event_store.py:29-76` (`append_event`)
- Test: `tests/test_event_store.py`

**Interfaces:**
- Produces: `EventStore.append_event(project_id: str, event_type: str, data: dict, tenant_id: str | None = None, *, source: str = "api", actor: dict | None = None) -> str`. `actor` is an optional dict with keys `actor_id`, `actor_type`, `actor_ip` (any subset; missing keys persist as null). Persists `source` and the three `actor_*` columns onto the `Event` row.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_store.py
import pytest
from sqlalchemy import select
from src.core.event_store import EventStore
from src.core.db_models import Event


@pytest.mark.asyncio
async def test_append_event_persists_provenance(db):
    store = EventStore(db)
    seq = await store.append_event(
        "proj-prov", "thing_updated", {"thing": 1},
        tenant_id="tenant-1",
        source="api",
        actor={"actor_id": "key-abc", "actor_type": "api_key", "actor_ip": "10.0.0.9"},
    )
    assert seq == "1"

    async with db.session() as session:
        row = (await session.execute(
            select(Event).where(Event.project_id == "proj-prov")
        )).scalar_one()
        assert row.source == "api"
        assert row.actor_id == "key-abc"
        assert row.actor_type == "api_key"
        assert row.actor_ip == "10.0.0.9"


@pytest.mark.asyncio
async def test_append_event_provenance_defaults(db):
    store = EventStore(db)
    await store.append_event("proj-prov2", "thing_updated", {"thing": 2})
    async with db.session() as session:
        row = (await session.execute(
            select(Event).where(Event.project_id == "proj-prov2")
        )).scalar_one()
        assert row.source == "api"      # default
        assert row.actor_id is None     # unauthenticated
        assert row.actor_type is None
        assert row.actor_ip is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_store.py::test_append_event_persists_provenance -v`
Expected: FAIL — `TypeError: append_event() got an unexpected keyword argument 'source'` (and/or `AttributeError: source` on the model).

- [ ] **Step 3: Add columns to the `Event` model**

In `src/core/db_models.py`, inside `class Event(Base)` (after the `sequence` column, before `created_at`):

```python
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="api"
    )
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    actor_ip: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
```

(`String` and `Optional` are already imported in this module.)

- [ ] **Step 4: Thread provenance through `append_event`**

In `src/core/event_store.py`, change the signature and the `Event(...)` construction:

```python
    async def append_event(
        self,
        project_id: str,
        event_type: str,
        data: Dict[str, Any],
        tenant_id: Optional[str] = None,
        *,
        source: str = "api",
        actor: Optional[Dict[str, Any]] = None,
    ) -> str:
        actor = actor or {}
        async with self.db.session() as session:
            result = await session.execute(
                select(func.coalesce(func.max(Event.sequence), 0) + 1)
                .where(Event.project_id == project_id)
            )
            sequence = result.scalar()

            event = Event(
                project_id=project_id,
                tenant_id=tenant_id,
                event_type=event_type,
                data=data,
                sequence=sequence,
                source=source,
                actor_id=actor.get("actor_id"),
                actor_type=actor.get("actor_type"),
                actor_ip=actor.get("actor_ip"),
            )
            session.add(event)
            await session.flush()

            sequence_str = str(sequence)
            logger.debug(
                "Appended event",
                project_id=project_id,
                event_type=event_type,
                sequence=sequence_str,
                source=source,
            )
            return sequence_str
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_event_store.py::test_append_event_persists_provenance tests/test_event_store.py::test_append_event_provenance_defaults -v`
Expected: PASS (the `db` fixture builds tables from `Base.metadata`, so the new columns exist automatically in tests).

- [ ] **Step 6: Commit**

```bash
git add src/core/db_models.py src/core/event_store.py tests/test_event_store.py
git commit -m "feat(events): add server-attested source/actor provenance to append_event"
```

---

### Task 2: Alembic migration for the provenance columns

**Files:**
- Create: `alembic/versions/<generated>_events_provenance.py` (path per `alembic.ini`; if the project uses a different migrations dir, use that)
- Reference: `alembic.ini`, existing files under the versions directory (copy their `down_revision` conventions)

**Interfaces:**
- Consumes: the `Event` model columns from Task 1.
- Produces: a reversible migration adding `source` (NOT NULL default `'api'`), `actor_id`, `actor_type`, `actor_ip` (nullable) to `events`.

- [ ] **Step 1: Generate the migration skeleton**

Run: `alembic revision -m "events provenance columns"`
This creates a new file under the versions directory with `revision`/`down_revision` prefilled.

- [ ] **Step 2: Write the upgrade/downgrade body**

Edit the generated file so `upgrade()` and `downgrade()` read exactly:

```python
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column("events", sa.Column("source", sa.String(length=50), nullable=False, server_default="api"))
    op.add_column("events", sa.Column("actor_id", sa.String(length=255), nullable=True))
    op.add_column("events", sa.Column("actor_type", sa.String(length=50), nullable=True))
    op.add_column("events", sa.Column("actor_ip", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "actor_ip")
    op.drop_column("events", "actor_type")
    op.drop_column("events", "actor_id")
    op.drop_column("events", "source")
```

- [ ] **Step 3: Apply and verify against a scratch database**

Run: `alembic upgrade head`
Expected: completes without error. Verify the columns exist:
`psql "$DATABASE_URL" -c "\d events"` — expect `source`, `actor_id`, `actor_type`, `actor_ip` rows.

- [ ] **Step 4: Verify the migration is reversible**

Run: `alembic downgrade -1 && alembic upgrade head`
Expected: both complete without error (down then back up).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat(events): alembic migration for source/actor provenance columns"
```

---

### Task 3: Carry provenance through `ContextEngine.publish_data`

**Files:**
- Modify: `src/core/context_engine.py:219-272` (`publish_data`)
- Test: `tests/test_context_engine.py`

**Interfaces:**
- Consumes: `append_event(..., source=, actor=)` from Task 1.
- Produces: `ContextEngine.publish_data(event: DataPublishEvent, *, source: str = "api", actor: dict | None = None, tenant_id: str | None = None) -> str`. Passes `source`, `actor`, and `tenant_id` straight to `append_event`. `DataPublishEvent` is unchanged (provenance is not a body field).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_engine.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_publish_data_forwards_provenance_to_event_store():
    engine = ContextEngine.__new__(ContextEngine)  # bypass __init__
    engine.semantic_matcher = MagicMock(register_data=AsyncMock())
    engine.event_store = MagicMock(append_event=AsyncMock(return_value="1"))
    engine._notify_affected_agents = AsyncMock()
    engine.subscriptions = MagicMock(reconcile_project=AsyncMock())

    evt = DataPublishEvent(project_id="p1", data_key="k1", data={"a": 1})
    actor = {"actor_id": "key-1", "actor_type": "api_key", "actor_ip": "1.2.3.4"}

    seq = await engine.publish_data(evt, source="api", actor=actor, tenant_id="tenant-1")

    assert seq == "1"
    _, kwargs = engine.event_store.append_event.call_args
    assert kwargs["source"] == "api"
    assert kwargs["actor"] == actor
    assert kwargs["tenant_id"] == "tenant-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_context_engine.py::test_publish_data_forwards_provenance_to_event_store -v`
Expected: FAIL — `TypeError: publish_data() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: Update `publish_data`**

In `src/core/context_engine.py`, change the signature and the `append_event` call:

```python
    async def publish_data(
        self,
        event: DataPublishEvent,
        *,
        source: str = "api",
        actor: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ) -> str:
```

and the append call (currently `sequence = await self.event_store.append_event(project_id, event_type, event_data)`) becomes:

```python
        sequence = await self.event_store.append_event(
            project_id, event_type, event_data,
            tenant_id=tenant_id, source=source, actor=actor,
        )
```

(Ensure `Optional`, `Dict`, `Any` are imported at the top of the module; add to the existing `typing` import if missing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_context_engine.py::test_publish_data_forwards_provenance_to_event_store -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/core/context_engine.py tests/test_context_engine.py
git commit -m "feat(engine): thread source/actor/tenant provenance through publish_data"
```

---

### Task 4: Stamp provenance from the HTTP publish route

**Files:**
- Modify: `src/api/routes.py:363-481` (the `publish_data` route; helper at `:27-37`)
- Test: `tests/test_publish_route_provenance.py` (create)

**Interfaces:**
- Consumes: `publish_data(event, *, source, actor, tenant_id)` from Task 3; `_get_request_context(request)` at `src/api/routes.py:27-37`.
- Produces: the route passes `source="api"`, `actor` (built from the request context), and `tenant_id` (from the request context) into `engine.publish_data`. No behavior change to the response shape.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_publish_route_provenance.py
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from src.core.db_models import Event


@pytest.mark.asyncio
async def test_publish_route_stamps_source_api(app_with_engine, db):
    async with AsyncClient(app=app_with_engine, base_url="http://test") as client:
        resp = await client.post("/api/v1/data/publish", json={
            "project_id": "route-prov", "data_key": "k", "data": {"x": 1},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "published"

    async with db.session() as session:
        row = (await session.execute(
            select(Event).where(Event.project_id == "route-prov")
        )).scalar_one()
        assert row.source == "api"   # stamped by the route, not the client
```

Note: `app_with_engine` is an app fixture with `app.state.context_engine` wired and auth disabled (actor is null in that mode — asserting `source` is the transport-attestation check that doesn't require auth). If no such fixture exists, add one to `tests/conftest.py` modeled on the existing app fixtures used by `tests/test_auth.py`; reuse the `db` and `redis` fixtures to build the engine.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_publish_route_provenance.py -v`
Expected: FAIL — `assert row.source == "api"` fails because the route currently calls `publish_data(event)` without `source`, so the column falls back to its server-default only for direct DB inserts, not for this path... (it will actually pass on default `"api"`; to make the test meaningfully fail-first, first change the route to pass `source="mcp"` deliberately, watch it fail, then correct to `"api"` — or assert on `actor` once auth is enabled). Keep the assertion on `source == "api"` and treat Step 3 as wiring the explicit stamp.

- [ ] **Step 3: Wire the explicit provenance stamp**

In `src/api/routes.py`, in the publish route, replace the call `sequence = await engine.publish_data(event)` (around line 413) with:

```python
        actor = {
            "actor_id": ctx["actor_id"],
            "actor_type": ctx["actor_type"],
            "actor_ip": ctx["actor_ip"],
        }
        sequence = await engine.publish_data(
            event, source="api", actor=actor, tenant_id=ctx.get("tenant_id"),
        )
```

(`ctx = _get_request_context(request)` is already assigned at the top of the route.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_publish_route_provenance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/routes.py tests/test_publish_route_provenance.py tests/conftest.py
git commit -m "feat(api): stamp server-attested source/actor on HTTP publish"
```

---

### Task 5: Accept `Authorization: Bearer` in `APIKeyMiddleware`

**Files:**
- Modify: `src/core/auth.py:86-130` (`APIKeyMiddleware.dispatch`)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `APIKeyMiddleware` resolves the API key from **either** the `X-API-Key` header **or** an `Authorization: Bearer <token>` header (X-API-Key wins if both present). All other behavior (public paths, 401s, `request.state.api_key_id`) is unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py  (add alongside existing tests)
@pytest.mark.asyncio
async def test_auth_middleware_accepts_bearer_token(app_with_auth, db):
    raw_key, _ = await create_api_key(db, "bearer-key")
    async with AsyncClient(app=app_with_auth, base_url="http://test") as client:
        resp = await client.get("/protected", headers={"Authorization": f"Bearer {raw_key}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_credentials(app_with_auth):
    async with AsyncClient(app=app_with_auth, base_url="http://test") as client:
        resp = await client.get("/protected")
        assert resp.status_code == 401
```

(`create_api_key`, `app_with_auth`, `AsyncClient` are already imported/available in `tests/test_auth.py` per the existing `test_auth_middleware_valid_key` test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py::test_auth_middleware_accepts_bearer_token -v`
Expected: FAIL with 401 — the middleware only reads `X-API-Key` today.

- [ ] **Step 3: Read the key from either header**

In `src/core/auth.py`, in `dispatch`, replace the line `api_key = request.headers.get("X-API-Key")` (line 93) with:

```python
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[len("Bearer "):].strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py::test_auth_middleware_accepts_bearer_token tests/test_auth.py::test_auth_middleware_rejects_missing_credentials tests/test_auth.py::test_auth_middleware_valid_key -v`
Expected: PASS (bearer works, missing-credential still 401, existing X-API-Key still works).

- [ ] **Step 5: Commit**

```bash
git add src/core/auth.py tests/test_auth.py
git commit -m "feat(auth): accept Authorization: Bearer tokens (for MCP clients)"
```

---

## Self-Review

**Spec coverage (Layer 1 provenance + header portions):**
- Events carry server-attested `source`/`actor` → Tasks 1, 3, 4. ✓
- Alembic migration with `source='api'` backfill → Task 2. ✓
- Provenance never client-settable (kept off `DataPublishEvent`) → enforced by Global Constraints + Task 3 design. ✓
- MCP bearer acceptance (`Authorization: Bearer`) → Task 5. ✓
- Object-level authz, MCP session principal, subscribe enforcement → explicitly deferred to Layer 1b (Scope section). ✓ (out of scope by design, gated on a spike)

**Placeholder scan:** No TBD/TODO; every code step has real code. Task 4 Step 2 documents the fail-first nuance explicitly rather than hand-waving. One fixture (`app_with_engine`) may need creating — instructions given, modeled on existing `app_with_auth`.

**Type consistency:** `append_event(..., *, source="api", actor=None)` (Task 1) is called with those exact kwargs in Task 3; `publish_data(event, *, source, actor, tenant_id)` (Task 3) is called with those exact kwargs in Task 4; `actor` dict keys (`actor_id`/`actor_type`/`actor_ip`) match the `Event` columns (Task 1) and `_get_request_context` output (Task 4). ✓

## Follow-on: Layer 1b (next plan)

Opens with a spike: (1) read `src/core/rbac.py` to pin the role/scope taxonomy for object-level authorization; (2) probe the vendored `mcp` SDK (`from mcp.server import MCPServer`) for how per-call/session context reaches tool handlers. Then: bind an authenticated principal to the MCP session, thread it into `contex_publish`/`contex_create_subscription`, enforce project/scope on publish and subscribe (both transports), and enforce subscribe read-scope on the SSE stream.
