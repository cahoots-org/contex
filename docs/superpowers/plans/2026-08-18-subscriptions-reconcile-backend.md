# Semantic Subscriptions — Backend Foundation (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent semantic subscriptions with materialized bundles, a `Matcher` seam, and a `reconcile` operation wired into publish — so an agent's matched context is stored and kept current, testable end-to-end at the service layer before any MCP or UI is added.

**Architecture:** A new `Subscription` model stores a natural-language need-set plus a *materialized bundle* (the current matched items) in Postgres. A `Matcher` seam abstracts relevance matching (one implementation delegates to the existing `SemanticDataMatcher.match_agent_needs`, which already uses Plan 1's hybrid search). A `SubscriptionService` creates subscriptions, serves their materialized bundle as a pull, and `reconcile`s them on data change: it recomputes each affected subscription's bundle fully, atomically swaps the stored bundle (buffer-until-complete), and emits a lightweight per-subscription "updated" event on Redis. `ContextEngine.publish_data` calls reconcile inline after storing. No MCP, no new REST endpoints (REST stays frozen); the MCP layer and sandbox are later plans that consume this.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, pgvector, Postgres FTS (Plan 1), Redis (pub/sub), pytest + pytest-asyncio, fakeredis, sentence-transformers (all-MiniLM-L6-v2, 384-dim).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-18-semantic-subscriptions-design.md`. This plan implements §4.2 (`reconcile`, sync-inline, buffer-until-complete), §4.3 (`Matcher` seam + naive matcher), §4.4 (subscription + materialized-bundle persistence). It does NOT implement §4.1 (MCP layer) or §4.5 (sandbox) — those are later plans.
- **REST stays frozen** (reposition spec §3.6): do NOT add new REST endpoints in this plan. Subscriptions are exercised through the service/engine layer and tests only.
- Vector storage is pgvector only; embedding dim 384. Do not reintroduce OpenSearch.
- The matched-bundle shape is exactly what `SemanticDataMatcher.match_agent_needs(project_id, needs)` returns: `dict[str (need) -> list[dict]]`, where each inner dict has keys `data_key`, `similarity` (float), `data` (dict), `description` (str|None). Store and return this shape verbatim.
- DB-dependent tests use the existing `db` fixture in `tests/conftest.py` (real Postgres `contex_test`, pgvector enabled, `Base.metadata.create_all`). Redis tests use the `redis` fixture (fakeredis). Both in `tests/conftest.py`.
- Do NOT run `git push`. Implementers do NOT commit (a subagent guardrail rejects relayed commit consent); the controller commits each task. (See ledger note from Plan 1.)
- Reconcile is **naive on purpose**: affected subscriptions = *all subscriptions in the changed item's project*. The symmetric-index optimization is explicitly deferred (spec §9). Do not build it here.

---

### Task 1: `Subscription` model + materialized bundle (model + migration)

**Files:**
- Modify: `src/core/db_models.py` (add `Subscription` class near the other models, e.g. after `AgentRegistration` ~line 407)
- Create: `alembic/versions/<rev>_add_subscriptions.py`
- Test: `tests/test_subscription_model.py`

**Interfaces:**
- Consumes: `Base`, existing model patterns in `db_models.py`.
- Produces: `Subscription` ORM model, table `subscriptions`, columns: `subscription_id` (str PK), `project_id` (str), `tenant_id` (str|None), `needs` (list[str]), `scope` (dict|None), `bundle` (dict, default empty dict — the materialized matches), `bundle_updated_at` (datetime|None), `created_at`, `updated_at`. Index `idx_subscriptions_project` on `project_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscription_model.py
import pytest
from sqlalchemy import select
from src.core.db_models import Subscription


@pytest.mark.asyncio
async def test_subscription_persists_needs_and_bundle(db):
    async with db.session() as session:
        session.add(Subscription(
            subscription_id="sub_1", project_id="p1",
            needs=["auth config"], scope=None,
            bundle={"auth config": [{"data_key": "cfg", "similarity": 0.9, "data": {}, "description": "d"}]},
        ))
        await session.commit()

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == "sub_1")
        )).scalar_one()
        assert row.needs == ["auth config"]
        assert row.bundle["auth config"][0]["data_key"] == "cfg"
        assert row.project_id == "p1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subscription_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'Subscription'`.

- [ ] **Step 3: Add the model**

In `src/core/db_models.py`, add (imports `JSONB`, `ARRAY`, `Text`, `String`, `DateTime`, `func`, `Index`, `Mapped`, `mapped_column` already exist in the file):

```python
class Subscription(Base):
    """A persistent semantic subscription: needs + a materialized matched bundle."""

    __tablename__ = "subscriptions"

    subscription_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    needs: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    scope: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    # Materialized matches, shape = SemanticDataMatcher.match_agent_needs output.
    bundle: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    bundle_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_subscriptions_project", "project_id"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subscription_model.py -v`
Expected: PASS (the `db` fixture's `create_all` builds the table).

- [ ] **Step 5: Create the Alembic migration**

Generate a revision with `down_revision` = current head (`002`; run `alembic heads` to confirm). Replace upgrade/downgrade:

```python
def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", sa.String(255), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=True),
        sa.Column("needs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=True),
        sa.Column("bundle", postgresql.JSONB(), nullable=False),
        sa.Column("bundle_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_subscriptions_project", "subscriptions", ["project_id"])


def downgrade() -> None:
    op.drop_index("idx_subscriptions_project", table_name="subscriptions")
    op.drop_table("subscriptions")
```

Ensure imports: `import sqlalchemy as sa`, `from sqlalchemy.dialects import postgresql`.

- [ ] **Step 6: Verify the migration on a scratch DB** (do NOT run against `contex_test`, which the `db` fixture builds via `create_all` — it would collide). Note: the pre-existing `001` migration cannot `alembic upgrade head` on a fresh DB (known asyncpg bytea→vector bug, tracked separately). Verify just the new revision by stamping to `002` first:

```
docker compose exec -T postgres createdb -U contex contex_migtest
# Point alembic at contex_migtest (check alembic/env.py for the URL env var; it likely wants a sync URL).
# Apply the pre-002 schema via create_all + `alembic stamp 002`, then:
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
docker compose exec -T postgres dropdb -U contex contex_migtest
```
Report the exact commands + output. If the 001 bug blocks even the stamp approach, report it and rely on the `create_all` test (Step 4) as the correctness gate for the schema.

- [ ] **Step 7: Commit** (controller performs this)

```bash
git add src/core/db_models.py alembic/versions/
git commit -m "feat: add Subscription model with materialized bundle"
```

---

### Task 2: The `Matcher` seam + `HybridMatcher`

**Files:**
- Create: `src/core/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `SemanticDataMatcher` (existing, `src/core/semantic_matcher.py`) and its `async match_agent_needs(project_id, needs) -> dict[str, list[dict]]`.
- Produces:
  - `Matcher` (Protocol): `async def match(self, project_id: str, needs: list[str], metadata: dict | None = None) -> dict[str, list[dict]]`.
  - `HybridMatcher(semantic_matcher)` implementing it by delegating to `match_agent_needs` (which already routes through Plan 1's `HybridSearchService` when hybrid is enabled, else pgvector). The `metadata` param is accepted and ignored — it is the seam a future LLM/tiered matcher routes on.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_matcher.py
import pytest
from src.core.matcher import HybridMatcher


class _FakeSemanticMatcher:
    def __init__(self):
        self.calls = []

    async def match_agent_needs(self, project_id, needs):
        self.calls.append((project_id, tuple(needs)))
        return {n: [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}] for n in needs}


@pytest.mark.asyncio
async def test_hybrid_matcher_delegates_and_returns_bundle_shape():
    sem = _FakeSemanticMatcher()
    matcher = HybridMatcher(sem)
    result = await matcher.match("p1", ["auth config"], metadata={"format": "json"})
    assert result == {"auth config": [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}]}
    assert sem.calls == [("p1", ("auth config",))]  # metadata is accepted but not passed through (seam only)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_matcher.py -v`
Expected: FAIL — `No module named 'src.core.matcher'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/matcher.py
"""The Matcher seam: relevance matching behind a stable interface.

HybridMatcher delegates to SemanticDataMatcher.match_agent_needs, which already
routes through Plan 1's HybridSearchService (pgvector + Postgres FTS + RRF) when
hybrid is enabled. `metadata` (item format/type/length) is accepted so a future
LLM or tiered-model matcher can route on it without changing callers.
"""
from __future__ import annotations

from typing import Any, Protocol


class Matcher(Protocol):
    async def match(
        self, project_id: str, needs: list[str], metadata: dict | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        ...


class HybridMatcher:
    def __init__(self, semantic_matcher) -> None:
        self.semantic_matcher = semantic_matcher

    async def match(
        self, project_id: str, needs: list[str], metadata: dict | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        return await self.semantic_matcher.match_agent_needs(project_id, needs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_matcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/matcher.py tests/test_matcher.py
git commit -m "feat: add Matcher seam and HybridMatcher"
```

---

### Task 3: `SubscriptionService.create` + `get_bundle` (persist, materialize, pull)

**Files:**
- Create: `src/core/subscriptions.py`
- Test: `tests/test_subscription_service.py`

**Interfaces:**
- Consumes: `Subscription` (Task 1), `Matcher`/`HybridMatcher` (Task 2), `DatabaseManager` (`.session()`), a Redis client.
- Produces: `SubscriptionService(db, matcher, redis)` with:
  - `async def create(self, project_id: str, needs: list[str], tenant_id: str | None = None, scope: dict | None = None, subscription_id: str | None = None) -> str` — persists a `Subscription`, runs an initial match, stores the bundle, returns the `subscription_id` (generated via `uuid4` hex with `sub_` prefix if not supplied).
  - `async def get_bundle(self, subscription_id: str) -> dict` — returns the stored materialized `bundle` (the pull). Raises `KeyError` if unknown.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscription_service.py
import pytest
from src.core.subscriptions import SubscriptionService
from src.core.db_models import Embedding


class _StubMatcher:
    async def match(self, project_id, needs, metadata=None):
        return {n: [{"data_key": "cfg", "similarity": 0.9, "data": {"x": 1}, "description": "auth"}] for n in needs}


@pytest.mark.asyncio
async def test_create_materializes_bundle_and_get_bundle_reads_it(db, redis):
    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["auth config"])
    assert sub_id.startswith("sub_")

    bundle = await svc.get_bundle(sub_id)
    assert bundle["auth config"][0]["data_key"] == "cfg"


@pytest.mark.asyncio
async def test_get_bundle_unknown_raises(db, redis):
    svc = SubscriptionService(db, _StubMatcher(), redis)
    with pytest.raises(KeyError):
        await svc.get_bundle("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subscription_service.py -v`
Expected: FAIL — `No module named 'src.core.subscriptions'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/core/subscriptions.py
"""Persistent semantic subscriptions with materialized bundles + reconcile."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from src.core.db_models import Subscription


class SubscriptionService:
    def __init__(self, db, matcher, redis) -> None:
        self.db = db
        self.matcher = matcher
        self.redis = redis

    async def create(
        self, project_id, needs, tenant_id=None, scope=None, subscription_id=None
    ) -> str:
        sub_id = subscription_id or f"sub_{uuid4().hex}"
        bundle = await self.matcher.match(project_id, needs)
        async with self.db.session() as session:
            session.add(Subscription(
                subscription_id=sub_id, project_id=project_id, tenant_id=tenant_id,
                needs=list(needs), scope=scope, bundle=bundle,
                bundle_updated_at=datetime.now(timezone.utc),
            ))
            await session.commit()
        return sub_id

    async def get_bundle(self, subscription_id) -> dict:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
        if row is None:
            raise KeyError(subscription_id)
        return row.bundle
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subscription_service.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/subscriptions.py tests/test_subscription_service.py
git commit -m "feat: add SubscriptionService create + get_bundle"
```

---

### Task 4: `SubscriptionService.reconcile_project` (recompute, buffer-swap, diff, emit)

**Files:**
- Modify: `src/core/subscriptions.py`
- Test: `tests/test_subscription_reconcile.py`

**Interfaces:**
- Consumes: `Subscription`, `Matcher`, `db`, `redis` (from Task 3).
- Produces: `async def reconcile_project(self, project_id: str, changed_data_key: str | None = None) -> list[str]` on `SubscriptionService`. For **every** subscription in `project_id` (naive — `changed_data_key` is accepted for a future narrowing but currently unused), recompute its bundle via `self.matcher.match`, compare to the stored bundle, and **only if changed**: atomically write the new bundle + `bundle_updated_at` (buffer-until-complete — compute fully, then one UPDATE), and publish `json.dumps({"subscription_id": id, "updated_at": <iso>})` to Redis channel `f"subscription:{id}:updated"`. Returns the list of changed subscription_ids.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscription_reconcile.py
import json
import pytest
from src.core.subscriptions import SubscriptionService


class _MutableMatcher:
    """Returns whatever bundle it's currently told to."""
    def __init__(self, bundle):
        self._bundle = bundle

    async def match(self, project_id, needs, metadata=None):
        return {n: self._bundle for n in needs}


@pytest.mark.asyncio
async def test_reconcile_updates_changed_bundle_and_emits(db, redis):
    m = _MutableMatcher([{"data_key": "cfg", "similarity": 0.9, "data": {"v": 1}, "description": "d"}])
    svc = SubscriptionService(db, m, redis)
    sub_id = await svc.create("p1", ["auth"])

    pubsub = redis.pubsub()
    await pubsub.subscribe(f"subscription:{sub_id}:updated")

    # data changes -> matcher now returns a different bundle
    m._bundle = [{"data_key": "cfg", "similarity": 0.95, "data": {"v": 2}, "description": "d"}]
    changed = await svc.reconcile_project("p1")

    assert changed == [sub_id]
    assert (await svc.get_bundle(sub_id))["auth"][0]["data"] == {"v": 2}
    # an updated event was published for this subscription
    msg = None
    for _ in range(5):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if msg:
            break
    assert msg is not None and json.loads(msg["data"])["subscription_id"] == sub_id


@pytest.mark.asyncio
async def test_reconcile_no_change_no_emit(db, redis):
    m = _MutableMatcher([{"data_key": "cfg", "similarity": 0.9, "data": {"v": 1}, "description": "d"}])
    svc = SubscriptionService(db, m, redis)
    sub_id = await svc.create("p1", ["auth"])
    # matcher returns the same bundle -> no change
    changed = await svc.reconcile_project("p1")
    assert changed == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subscription_reconcile.py -v`
Expected: FAIL — `AttributeError: 'SubscriptionService' object has no attribute 'reconcile_project'`.

- [ ] **Step 3: Add the method**

Append to `SubscriptionService` in `src/core/subscriptions.py`:

```python
    async def reconcile_project(self, project_id, changed_data_key=None) -> list[str]:
        async with self.db.session() as session:
            subs = (await session.execute(
                select(Subscription).where(Subscription.project_id == project_id)
            )).scalars().all()

        changed_ids: list[str] = []
        for sub in subs:
            new_bundle = await self.matcher.match(project_id, sub.needs)  # computed fully first
            if new_bundle == sub.bundle:
                continue
            async with self.db.session() as session:  # buffer-until-complete: one atomic swap
                row = (await session.execute(
                    select(Subscription).where(Subscription.subscription_id == sub.subscription_id)
                )).scalar_one()
                row.bundle = new_bundle
                row.bundle_updated_at = datetime.now(timezone.utc)
                await session.commit()
            await self.redis.publish(
                f"subscription:{sub.subscription_id}:updated",
                json.dumps({
                    "subscription_id": sub.subscription_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }),
            )
            changed_ids.append(sub.subscription_id)
        return changed_ids
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subscription_reconcile.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/subscriptions.py tests/test_subscription_reconcile.py
git commit -m "feat: add reconcile_project with buffer-until-complete swap and emit"
```

---

### Task 5: Wire `reconcile` into `ContextEngine.publish_data`

**Files:**
- Modify: `src/core/context_engine.py` (`__init__` ~lines 38–73, `publish_data` ~lines 211–254)
- Test: `tests/test_engine_reconcile_integration.py`

**Interfaces:**
- Consumes: `SubscriptionService` (Task 4), `HybridMatcher` (Task 2).
- Produces: `ContextEngine` gains `self.subscriptions: SubscriptionService` (built in `__init__` from `HybridMatcher(self.semantic_matcher)`, `self.db`, `self.redis`), and `publish_data` calls `await self.subscriptions.reconcile_project(project_id, data_key)` after the event is appended (after existing line 252's notify call). Existing `_notify_affected_agents` behavior is left intact (legacy path, untouched).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_reconcile_integration.py
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_publish_reconciles_matching_subscription(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    # A subscription whose need should match the doc we publish.
    sub_id = await engine.subscriptions.create("proj", ["database connection settings"])

    await engine.publish_data(DataPublishEvent(
        project_id="proj", data_key="db_cfg",
        data={"host": "localhost", "port": 5432, "purpose": "database connection settings"},
        data_format="json",
    ))

    bundle = await engine.subscriptions.get_bundle(sub_id)
    # the published item now appears in the subscription's materialized bundle
    all_keys = [m["data_key"] for matches in bundle.values() for m in matches]
    assert any("db_cfg" in k for k in all_keys)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine_reconcile_integration.py -v`
Expected: FAIL — `AttributeError: 'ContextEngine' object has no attribute 'subscriptions'`.

- [ ] **Step 3: Wire it in**

In `context_engine.py`, add imports at top:

```python
from src.core.matcher import HybridMatcher
from src.core.subscriptions import SubscriptionService
```

In `ContextEngine.__init__`, after `self.semantic_matcher = ...` (and after `self.redis = redis`), add:

```python
        self.subscriptions = SubscriptionService(
            db, HybridMatcher(self.semantic_matcher), redis
        )
```

In `publish_data`, after the existing `await self._notify_affected_agents(...)` call (line ~252), add:

```python
        # Reconcile persistent subscriptions against the new data (inline).
        await self.subscriptions.reconcile_project(project_id, data_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine_reconcile_integration.py -v`
Expected: PASS. (Uses the real matcher end-to-end; requires the model — first run downloads all-MiniLM-L6-v2. The huggingface `resume_download` FutureWarning is expected upstream noise.)

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/context_engine.py tests/test_engine_reconcile_integration.py
git commit -m "feat: reconcile subscriptions inline on publish"
```

---

### Task 6: Query↔subscription consistency + delete/cleanup

**Files:**
- Modify: `src/core/subscriptions.py` (add `delete`)
- Test: `tests/test_subscription_consistency.py`

**Interfaces:**
- Consumes: `SubscriptionService` (Tasks 3–4), `ContextEngine.query_project_data` (existing, `context_engine.py:512`).
- Produces: `async def delete(self, subscription_id: str) -> None` on `SubscriptionService` (removes the row; no error if absent). Plus a test locking the spec's trust property: for the same need + corpus, `contex_query`'s matches equal a freshly-created subscription's materialized bundle.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subscription_consistency.py
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_query_matches_subscription_bundle(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    await engine.publish_data(DataPublishEvent(
        project_id="proj", data_key="db_cfg",
        data={"purpose": "database connection settings", "port": 5432}, data_format="json",
    ))

    need = "database connection settings"
    query_matches = await engine.query_project_data("proj", need, top_k=10, threshold=0.1)
    sub_id = await engine.subscriptions.create("proj", [need])
    bundle_matches = (await engine.subscriptions.get_bundle(sub_id))[need]

    # what you test (query) is what you get (subscription): same data_keys, same order
    assert [m["data_key"] for m in query_matches] == [m["data_key"] for m in bundle_matches]


@pytest.mark.asyncio
async def test_delete_removes_subscription(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    sub_id = await engine.subscriptions.create("proj", ["anything"])
    await engine.subscriptions.delete(sub_id)
    with pytest.raises(KeyError):
        await engine.subscriptions.get_bundle(sub_id)
    await engine.subscriptions.delete(sub_id)  # idempotent — no error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_subscription_consistency.py -v`
Expected: FAIL — `AttributeError: ... 'delete'` (and/or the consistency assertion if `delete` is stubbed).

- [ ] **Step 3: Add `delete`**

Append to `SubscriptionService`:

```python
    async def delete(self, subscription_id) -> None:
        async with self.db.session() as session:
            row = (await session.execute(
                select(Subscription).where(Subscription.subscription_id == subscription_id)
            )).scalar_one_or_none()
            if row is not None:
                await session.delete(row)
                await session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_subscription_consistency.py -v`
Expected: PASS. If the consistency assertion fails, do NOT weaken it — it means `create`'s matcher path and `query_project_data` diverge; investigate and reconcile them (both must ultimately call `match_agent_needs` for the same need). Report if they genuinely cannot be aligned.

- [ ] **Step 5: Commit** (controller)

```bash
git add src/core/subscriptions.py tests/test_subscription_consistency.py
git commit -m "feat: add subscription delete and lock query/bundle consistency"
```

---

## Self-Review Notes

- **Spec coverage:** §4.4 persistence → Task 1; §4.3 Matcher seam + naive matcher → Task 2; §4.2 reconcile (sync-inline, buffer-until-complete) → Tasks 4–5; the "saved live query" consistency property (§4.1) → Task 6. Deferred correctly (other plans): §4.1 MCP layer, §4.5 sandbox, §9 scale engine. REST stays frozen (no new endpoints) per Global Constraints.
- **Placeholder scan:** no TBD/TODO; each step carries runnable code. The one flagged verification (alembic env URL var, Task 1 Step 6) is a check against a real file, and the known 001-migration bug is called out with a fallback.
- **Type consistency:** the bundle shape (`dict[need -> list[{data_key, similarity, data, description}]]`) is identical across `match_agent_needs`, `Matcher.match`, `Subscription.bundle`, `create`, `get_bundle`, and `reconcile_project`. `subscription_id` is the id everywhere; `reconcile_project` returns `list[str]` of changed ids consumed by nobody downstream yet (the MCP plan will consume the Redis events, not the return value).
- **Reuse:** matching goes through the existing `match_agent_needs` (which already uses Plan 1's hybrid search) rather than rebuilding; legacy `_notify_affected_agents` is left untouched.
