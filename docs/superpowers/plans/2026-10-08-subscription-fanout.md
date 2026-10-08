# Subscription Fan-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A publish reconciles only the subscriptions it can affect, once per publish call, with a periodic sweep for eventual consistency.

**Architecture:** `reconcile_project(project_id, changed_keys)` narrows to the union of three checks: the bundle already includes a changed document (`subscriptions.documents && K`), a changed document defines a symbol a bundled document references, or a changed node's embedding clears a need's admission floor. Need vectors are cached in process memory; floors are computed in SQL from the stored bundle. A background sweep, gated by a Postgres advisory lock, runs full reconciles on an interval.

**Tech Stack:** Python 3, SQLAlchemy async, Postgres (ParadeDB: pgvector + pg_search), alembic, numpy, pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-07-subscription-fanout-design.md`

## Global Constraints

- No new persisted per-need state. No `subscription_needs` table.
- `subscriptions.documents` holds only document keys already present in `bundle`.
- A reconcile failure never fails a publish.
- Any filter failure falls back to reconciling every subscription in the project.
- `RECONCILE_SWEEP_SECONDS` default `300`; `0` disables the sweep.
- Embeddings are 768-dim, L2-normalized (`OnnxEmbedder.encode`), so cosine = dot product.
- No inline imports, no meta comments, concise docstrings (Rob's style).
- Each task is one commit, CI-green. Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HSVMS4uK4G3oL7bAxmY2Ut
  ```

## Review Focus

1. A publish into a project with no subscriptions, or of keys with no embedded nodes (binary formats), must not error and must reconcile nothing. Pinned in Task 4.
2. Subscriptions stored with `top_k=None` or `threshold=None` must use the matcher defaults for the floor, not crash on `None`. Pinned in Task 4.
3. A need whose bundle list is empty must use the threshold as its floor. Pinned in Task 4.
4. An encoder exception during filtering must fall back to reconciling everything, not drop the update. Pinned in Task 4.
5. A subscription deleted between filter and reconcile must be skipped quietly. Pinned in Task 3.

---

### Task 1: One reconcile per publish call

**Files:**
- Modify: `src/core/context_engine.py` (`publish_data_batch`, `_record_publish`)
- Modify: `src/core/subscriptions.py` (`reconcile_project` signature/docstring)
- Test: `tests/test_context_engine.py`

**Interfaces:**
- Produces: `SubscriptionService.reconcile_project(project_id: str, changed_keys: set[str] | None = None) -> list[str]`. `None` means reconcile everything. In this task `changed_keys` is accepted and still unused.

- [ ] **Step 1: Write the failing test** (append to `tests/test_context_engine.py`, module level)

```python
@pytest.mark.asyncio
async def test_publish_batch_reconciles_once_with_all_keys():
    engine = ContextEngine.__new__(ContextEngine)
    engine.semantic_matcher = MagicMock(register_data_batch=AsyncMock())
    engine.event_store = MagicMock(append_event=AsyncMock(side_effect=["1", "2", "3"]))
    engine.subscriptions = MagicMock(reconcile_project=AsyncMock())

    events = [DataPublishEvent(project_id="p1", data_key=k, data={"a": 1}) for k in ("k1", "k2", "k3")]
    seqs = await engine.publish_data_batch(events)

    assert seqs == ["1", "2", "3"]
    engine.subscriptions.reconcile_project.assert_awaited_once_with("p1", {"k1", "k2", "k3"})


@pytest.mark.asyncio
async def test_publish_survives_reconcile_failure():
    engine = ContextEngine.__new__(ContextEngine)
    engine.semantic_matcher = MagicMock(register_data_batch=AsyncMock())
    engine.event_store = MagicMock(append_event=AsyncMock(return_value="1"))
    engine.subscriptions = MagicMock(reconcile_project=AsyncMock(side_effect=RuntimeError("boom")))

    seq = await engine.publish_data(DataPublishEvent(project_id="p1", data_key="k1", data={"a": 1}))

    assert seq == "1"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_context_engine.py -k "reconciles_once or survives_reconcile" -v`
Expected: first test FAILS (`reconcile_project` awaited 3 times, with `"k1"` etc.).

- [ ] **Step 3: Implement**

In `_record_publish`, delete the `try: await self.subscriptions.reconcile_project(project_id, data_key) ... except` block and its comment; update its docstring to `"""Append one published item to the event store."""`.

In `publish_data_batch`, replace the final `return [...]` with:

```python
        sequences = [
            await self._record_publish(e, data, fmt, source=source, actor=actor, tenant_id=tenant_id)
            for e, data, fmt in prepared
        ]

        # Reconcile once for the whole batch. The events are already appended,
        # so a reconcile failure must not fail the publish (spec §6).
        try:
            await self.subscriptions.reconcile_project(project_id, {e.data_key for e in events})
        except Exception:
            logger.exception("subscription reconcile failed for %s", project_id)

        return sequences
```

In `src/core/subscriptions.py`, rename the parameter `changed_data_key=None` to `changed_keys: set[str] | None = None` and replace the docstring's last sentence with: "`changed_keys` names the documents just published; `None` reconciles every subscription."

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_context_engine.py tests/test_subscription_reconcile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/context_engine.py src/core/subscriptions.py tests/test_context_engine.py
git commit -m "Reconcile subscriptions once per publish call

A batch of N items ran N full project reconciles. Collect the published
keys and reconcile once after all events are appended."
```

---

### Task 2: `subscriptions.documents`

**Files:**
- Create: `alembic/versions/016_subscription_documents.py`
- Modify: `src/core/db_models.py` (`Subscription`)
- Modify: `src/core/subscriptions.py` (`create`, `reconcile_project`, `_link_bundle`, new `_bundle_documents`)
- Test: `tests/test_subscription_documents.py`

**Interfaces:**
- Produces: `Subscription.documents: list[str]` (GIN-indexed `text[]`), kept equal to `_bundle_documents(bundle)` on create and every changed reconcile.
- Produces: `_bundle_documents(bundle: dict) -> list[str]` — sorted distinct document keys of every match (`document`, falling back to `data_key`) and every link (`document`, falling back to `data_key`).
- Produces: link entries in `_link_bundle` gain `"document": <the defining node's data_key>`.

- [ ] **Step 1: Write the failing tests** (`tests/test_subscription_documents.py`)

```python
import pytest
from sqlalchemy import select, text

from src.core.db_models import Subscription
from src.core.subscriptions import SubscriptionService, _bundle_documents


class _KeyedMatcher:
    def __init__(self, bundles):
        self.bundles = bundles

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        return {n: list(self.bundles.get(n, [])) for n in needs}


def _m(node, doc, sim=0.9, links=()):
    entry = {"data_key": node, "document": doc, "similarity": sim, "data": {}, "description": "d", "related": []}
    if links:
        entry["links"] = list(links)
    return entry


def test_bundle_documents_collects_matches_and_links():
    bundle = {
        "a": [_m("x.py.f", "x.py", links=[{"data_key": "y.py.g", "document": "y.py", "name": "g"}])],
        "b": [_m("z.md#h", "z.md"), {"data_key": "legacy", "similarity": 0.5}],
    }
    assert _bundle_documents(bundle) == ["legacy", "x.py", "y.py", "z.md"]


async def _documents(db, sub_id):
    async with db.session() as s:
        return (await s.execute(
            select(Subscription.documents).where(Subscription.subscription_id == sub_id)
        )).scalar_one()


@pytest.mark.asyncio
async def test_create_and_reconcile_write_documents(db):
    m = _KeyedMatcher({"auth": [_m("a.py.f", "a.py")]})
    svc = SubscriptionService(db, m)
    sub_id = await svc.create("p1", ["auth"])
    assert await _documents(db, sub_id) == ["a.py"]

    m.bundles["auth"] = [_m("b.py.g", "b.py")]
    await svc.reconcile_project("p1")
    assert await _documents(db, sub_id) == ["b.py"]


@pytest.mark.asyncio
async def test_documents_overlap_is_indexable(db):
    svc = SubscriptionService(db, _KeyedMatcher({"auth": [_m("a.py.f", "a.py")]}))
    sub_id = await svc.create("p1", ["auth"])
    async with db.session() as s:
        hits = (await s.execute(
            select(Subscription.subscription_id).where(Subscription.documents.overlap(["a.py", "q"]))
        )).scalars().all()
        index = await s.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_subscriptions_documents'"
        ))
    assert hits == [sub_id]
    assert "gin" in index.lower()
```

Also add to `tests/test_symbol_linking.py::test_matched_node_links_to_cross_file_def`, after the existing asserts:

```python
    assert links[0]["document"] == "repo:a.py"
```

Migration backfill test. Load the migration's SQL constant via `importlib` (alembic version files aren't importable by name); put this at module top with the other imports:

```python
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "m016", pathlib.Path(__file__).parent.parent / "alembic/versions/016_subscription_documents.py"
)
m016 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m016)
```

and the test:

```python
@pytest.mark.asyncio
async def test_migration_backfill_sql_derives_documents(db):
    svc = SubscriptionService(db, _KeyedMatcher({"auth": [_m("a.py.f", "a.py", links=[{"data_key": "b.py.g", "document": "b.py", "name": "g"}])]}))
    sub_id = await svc.create("p1", ["auth"])
    async with db.session() as s:
        await s.execute(text("UPDATE subscriptions SET documents = '{}'"))
        await s.execute(text(m016.BACKFILL_SQL))
    assert await _documents(db, sub_id) == ["a.py", "b.py"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_subscription_documents.py tests/test_symbol_linking.py -v`
Expected: FAIL (`ImportError: _bundle_documents`, missing migration file).

- [ ] **Step 3: Implement**

`alembic/versions/016_subscription_documents.py`:

```python
"""Index subscriptions by the documents their bundles touch

Lets a publish find subscriptions whose bundle already includes a changed
document without loading every bundle.

Revision ID: 016
Revises: 015
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = '016'
down_revision: Union[str, None] = '015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKFILL_SQL = """
UPDATE subscriptions s SET documents = COALESCE((
    SELECT array_agg(DISTINCT d ORDER BY d) FROM (
        SELECT COALESCE(m->>'document', m->>'data_key') AS d
          FROM jsonb_each(s.bundle) kv, jsonb_array_elements(kv.value) m
        UNION
        SELECT COALESCE(l->>'document', l->>'data_key')
          FROM jsonb_each(s.bundle) kv, jsonb_array_elements(kv.value) m,
               jsonb_array_elements(COALESCE(m->'links', '[]'::jsonb)) l
    ) x WHERE d IS NOT NULL
), '{}')
"""


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("documents", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "idx_subscriptions_documents", "subscriptions", ["documents"], postgresql_using="gin"
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("idx_subscriptions_documents", table_name="subscriptions")
    op.drop_column("subscriptions", "documents")
```

Pre-016 link entries have no `document`, so the backfill records their node key instead. That is harmless (it never overlaps a document key), and the first reconcile rewrites it because Step 3's link change alters the bundle.

`src/core/db_models.py`, in `Subscription` after `bundle`:

```python
    # Every document key the bundle touches; lets a publish find affected subscriptions.
    documents: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'"))
```

and add to `__table_args__`:

```python
        Index("idx_subscriptions_documents", "documents", postgresql_using="gin"),
```

`src/core/subscriptions.py`, module level after `_assert_sub_tenant`:

```python
def _bundle_documents(bundle) -> list[str]:
    """Sorted document keys of every match and link in a bundle."""
    docs = set()
    for matches in bundle.values():
        for m in matches:
            docs.add(m.get("document") or m["data_key"])
            docs.update(l.get("document") or l["data_key"] for l in m.get("links", ()))
    return sorted(docs)
```

In `create`, add `documents=_bundle_documents(bundle),` to the `Subscription(...)` constructor. In `reconcile_project`'s atomic swap, after `row.bundle = new_bundle` add `row.documents = _bundle_documents(new_bundle)`. In `_link_bundle`'s `links.append({...})`, add `"document": e.data_key,`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_subscription_documents.py tests/test_symbol_linking.py tests/test_subscription_reconcile.py tests/test_subscription_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/016_subscription_documents.py src/core/db_models.py src/core/subscriptions.py tests/test_subscription_documents.py tests/test_symbol_linking.py
git commit -m "Index subscriptions by the documents their bundles touch

Adds a GIN-indexed documents column, written with every bundle and
backfilled from existing bundles. Links now carry their document."
```

---

### Task 3: Narrow reconcile by documents and symbol links

**Files:**
- Modify: `src/core/subscriptions.py`
- Test: `tests/test_subscription_affected.py`

**Interfaces:**
- Consumes: `Subscription.documents`, `Symbol` rows (`project_id`, `data_key`, `name`, `role`).
- Produces on `SubscriptionService`:
  - `async _ids_including(project_id: str, keys: set[str]) -> set[str]`
  - `async _ids_linking(project_id: str, keys: set[str]) -> set[str]`
  - `async _ids_admitting(project_id: str, keys: set[str]) -> set[str] | None` — `None` means "cannot tell, assume all". In this task it always returns `None`; Task 4 implements it.
  - `async _affected(project_id: str, keys: set[str]) -> set[str] | None` — union of the three, `None` if any is `None`.
- `reconcile_project` uses `_affected` when `changed_keys` is not `None`, and falls back to all subscriptions on `None` or exception.

- [ ] **Step 1: Write the failing tests** (`tests/test_subscription_affected.py`)

```python
import pytest

from src.core.db_models import Symbol
from src.core.subscriptions import SubscriptionService


class _RecordingMatcher:
    """Fixed per-need bundles; records which needs were matched."""
    def __init__(self, bundles):
        self.bundles = bundles
        self.calls = []

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        self.calls.append(tuple(needs))
        return {n: [dict(m) for m in self.bundles.get(n, [])] for n in needs}


def _m(node, doc, sim=0.9):
    return {"data_key": node, "document": doc, "similarity": sim, "data": {}, "description": "d", "related": []}


async def _service(db):
    matcher = _RecordingMatcher({"a": [_m("a.py.f", "a.py")], "b": [_m("b.py.g", "b.py")]})
    svc = SubscriptionService(db, matcher)
    sub_a = await svc.create("p1", ["a"])
    sub_b = await svc.create("p1", ["b"])
    return svc, sub_a, sub_b


@pytest.mark.asyncio
async def test_ids_including_matches_bundled_documents(db):
    svc, sub_a, _ = await _service(db)
    assert await svc._ids_including("p1", {"a.py", "zzz"}) == {sub_a}
    assert await svc._ids_including("p2", {"a.py"}) == set()


@pytest.mark.asyncio
async def test_ids_linking_finds_refs_to_new_defs(db):
    svc, sub_a, _ = await _service(db)
    async with db.session() as s:
        s.add_all([
            Symbol(project_id="p1", data_key="a.py", node_key="a.py.f", name="helper", role="ref"),
            Symbol(project_id="p1", data_key="h.py", node_key="h.py.helper", name="helper", role="def"),
        ])
    assert await svc._ids_linking("p1", {"h.py"}) == {sub_a}
    assert await svc._ids_linking("p1", {"other.py"}) == set()


@pytest.mark.asyncio
async def test_affected_is_none_without_vector_check(db):
    svc, _, _ = await _service(db)
    assert await svc._affected("p1", {"a.py"}) is None


@pytest.mark.asyncio
async def test_reconcile_with_keys_only_touches_affected(db):
    svc, sub_a, _ = await _service(db)

    async def no_vector_hits(project_id, keys):
        return set()
    svc._ids_admitting = no_vector_hits
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"a.py"})

    assert svc.matcher.calls == [("a",)]


@pytest.mark.asyncio
async def test_reconcile_falls_back_to_all_when_filter_raises(db):
    svc, _, _ = await _service(db)

    async def broken(project_id, keys):
        raise RuntimeError("boom")
    svc._ids_including = broken
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"a.py"})

    assert sorted(svc.matcher.calls) == [("a",), ("b",)]


@pytest.mark.asyncio
async def test_reconcile_skips_subscription_deleted_mid_pass(db, caplog):
    svc, sub_a, sub_b = await _service(db)
    svc.matcher.bundles["a"] = [_m("a.py.f", "a.py", sim=0.1)]
    svc.matcher.bundles["b"] = [_m("b.py.g", "b.py", sim=0.1)]
    real_match = svc.matcher.match

    async def match_then_delete(project_id, needs, **kw):
        result = await real_match(project_id, needs, **kw)
        if needs == ["a"]:
            await svc.delete(sub_a)
        return result
    svc.matcher.match = match_then_delete

    changed = await svc.reconcile_project("p1")

    assert changed == [sub_b]
    assert "reconcile failed" not in caplog.text
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_subscription_affected.py -v`
Expected: FAIL (`AttributeError: _ids_including`; the deleted-mid-pass test fails on the logged `NoResultFound`).

- [ ] **Step 3: Implement** in `src/core/subscriptions.py`

Add methods on `SubscriptionService`:

```python
    async def _ids_including(self, project_id, keys) -> set[str]:
        """Subscriptions whose bundle already touches a changed document."""
        async with self.db.session() as session:
            return set((await session.execute(
                select(Subscription.subscription_id)
                .where(Subscription.project_id == project_id)
                .where(Subscription.documents.overlap(list(keys)))
            )).scalars())

    async def _ids_linking(self, project_id, keys) -> set[str]:
        """Subscriptions bundling a document that references a name a changed document defines."""
        defined = (
            select(Symbol.name)
            .where(Symbol.project_id == project_id)
            .where(Symbol.data_key.in_(keys))
            .where(Symbol.role == "def")
        )
        async with self.db.session() as session:
            referencing = list((await session.execute(
                select(Symbol.data_key).distinct()
                .where(Symbol.project_id == project_id)
                .where(Symbol.role == "ref")
                .where(Symbol.name.in_(defined))
            )).scalars())
        if not referencing:
            return set()
        return await self._ids_including(project_id, set(referencing))

    async def _ids_admitting(self, project_id, keys) -> set[str] | None:
        """Subscriptions a changed node could newly match; None when unknown."""
        return None

    async def _affected(self, project_id, keys) -> set[str] | None:
        """Subscriptions a publish of ``keys`` can change; None means all of them."""
        admitting = await self._ids_admitting(project_id, keys)
        if admitting is None:
            return None
        return (
            admitting
            | await self._ids_including(project_id, keys)
            | await self._ids_linking(project_id, keys)
        )
```

Replace the subscription load at the top of `reconcile_project` with:

```python
        affected = None
        if changed_keys is not None:
            try:
                affected = await self._affected(project_id, changed_keys)
            except Exception:
                logger.exception("affected-subscription filter failed for %s; reconciling all", project_id)

        stmt = select(Subscription).where(Subscription.project_id == project_id)
        if affected is not None:
            stmt = stmt.where(Subscription.subscription_id.in_(affected))
        async with self.db.session() as session:
            subs = (await session.execute(stmt)).scalars().all()
```

In the atomic swap, replace `.scalar_one()` with `.scalar_one_or_none()` and add directly after it:

```python
                    if row is None:  # deleted since the filter ran
                        continue
```

Because `continue` inside `async with` exits the session context (committing nothing), this is safe; the `changed_ids.append` after the block is skipped.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_subscription_affected.py tests/test_subscription_reconcile.py tests/test_subscription_documents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/subscriptions.py tests/test_subscription_affected.py
git commit -m "Narrow reconcile to subscriptions a publish can affect

Adds the document-overlap and symbol-link checks. The vector check is a
stub that defers to a full reconcile, so behavior is unchanged until it
lands. A subscription deleted mid-pass is now skipped quietly."
```

---

### Task 4: Vector admission check

**Files:**
- Modify: `src/core/subscriptions.py` (`__init__`, `_ids_admitting`, new `_floors`, `_need_vectors`)
- Modify: `src/core/context_engine.py` (`SubscriptionService(...)` construction)
- Test: `tests/test_subscription_admission.py`

**Interfaces:**
- Produces: `SubscriptionService(db, matcher, encoder=None, default_threshold=0.35, default_top_k=10)`. `encoder` has `encode(list[str]) -> np.ndarray (n, 768)`, L2-normalized. With `encoder=None`, `_ids_admitting` returns `None` (Task 3 behavior).
- Produces: `_ids_admitting(project_id, keys) -> set[str] | None`, and `_floors(project_id) -> list[tuple[str, str, float]]` of `(subscription_id, need, floor)`.
- Consumes: `_affected` from Task 3.

- [ ] **Step 1: Write the failing tests** (`tests/test_subscription_admission.py`)

```python
import numpy as np
import pytest

from src.core.db_models import Embedding
from src.core.subscriptions import SubscriptionService


def _unit(i):
    v = np.zeros(768, dtype=np.float32)
    v[i] = 1.0
    return v


def _blend(i, j, w):
    """Unit vector at cosine ``w`` to axis i, the rest on axis j."""
    v = np.zeros(768, dtype=np.float32)
    v[i], v[j] = w, np.sqrt(1 - w * w)
    return v


class _AxisEncoder:
    """Need text 'axisN' encodes to the unit vector on axis N."""
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return np.stack([_unit(int(t.removeprefix("axis"))) for t in texts])


class _RecordingMatcher:
    def __init__(self, bundles):
        self.bundles = bundles
        self.calls = []

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        self.calls.append(tuple(needs))
        return {n: [dict(m) for m in self.bundles.get(n, [])] for n in needs}


def _m(doc, sim):
    return {"data_key": f"{doc}.n", "document": doc, "similarity": sim, "data": {}, "description": "d", "related": []}


async def _node(db, project, doc, vec):
    async with db.session() as s:
        s.add(Embedding(project_id=project, data_key=doc, node_key=f"{doc}.new", data={}, embedding=vec.tolist()))


def _svc(db, bundles, encoder=None):
    return SubscriptionService(
        db, _RecordingMatcher(bundles), encoder=encoder or _AxisEncoder(),
        default_threshold=0.35, default_top_k=10,
    )


@pytest.mark.asyncio
async def test_full_bundle_floor_is_lowest_match(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9), _m("d2", 0.8)]})
    sub = await svc.create("p1", ["axis0"], top_k=2)
    assert await svc._floors("p1") == [(sub, "axis0", 0.8)]


@pytest.mark.asyncio
async def test_unfilled_or_empty_bundle_floor_is_threshold(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9)], "axis1": []})
    sub = await svc.create("p1", ["axis0", "axis1"], top_k=2, threshold=0.5)
    assert sorted(await svc._floors("p1")) == [(sub, "axis0", 0.5), (sub, "axis1", 0.5)]


@pytest.mark.asyncio
async def test_null_top_k_and_threshold_use_defaults(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9)]})
    sub = await svc.create("p1", ["axis0"])  # top_k=None, threshold=None
    assert await svc._floors("p1") == [(sub, "axis0", 0.35)]


@pytest.mark.asyncio
async def test_node_above_floor_admits_only_that_subscription(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9), _m("d2", 0.8)], "axis1": [_m("d3", 0.9), _m("d4", 0.8)]})
    sub0 = await svc.create("p1", ["axis0"], top_k=2)
    await svc.create("p1", ["axis1"], top_k=2)
    await _node(db, "p1", "new", _blend(0, 2, 0.85))

    assert await svc._ids_admitting("p1", {"new"}) == {sub0}


@pytest.mark.asyncio
async def test_node_below_floor_admits_nothing_and_reconcile_skips(db):
    svc = _svc(db, {"axis0": [_m("d1", 0.9), _m("d2", 0.8)]})
    await svc.create("p1", ["axis0"], top_k=2)
    await _node(db, "p1", "new", _blend(0, 2, 0.5))
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"new"})

    assert svc.matcher.calls == []


@pytest.mark.asyncio
async def test_keys_without_nodes_or_subscriptions_admit_nothing(db):
    svc = _svc(db, {})
    assert await svc._ids_admitting("p1", {"binary.pdf"}) == set()
    await _node(db, "p1", "new", _unit(0))
    assert await svc._ids_admitting("p1", {"new"}) == set()


@pytest.mark.asyncio
async def test_need_vectors_are_cached(db):
    enc = _AxisEncoder()
    svc = _svc(db, {"axis0": [_m("d1", 0.9)]}, encoder=enc)
    await svc.create("p1", ["axis0"])
    await _node(db, "p1", "new", _unit(0))
    await svc._ids_admitting("p1", {"new"})
    await svc._ids_admitting("p1", {"new"})
    assert enc.calls == 1


@pytest.mark.asyncio
async def test_encoder_failure_reconciles_everything(db):
    class _Broken:
        def encode(self, texts):
            raise RuntimeError("model gone")
    svc = _svc(db, {"axis0": [_m("d1", 0.9)], "axis1": [_m("d3", 0.9)]}, encoder=_Broken())
    await svc.create("p1", ["axis0"])
    await svc.create("p1", ["axis1"])
    await _node(db, "p1", "new", _unit(5))
    svc.matcher.calls.clear()

    await svc.reconcile_project("p1", {"new"})

    assert sorted(svc.matcher.calls) == [("axis0",), ("axis1",)]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_subscription_admission.py -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'encoder'`).

- [ ] **Step 3: Implement**

`src/core/subscriptions.py`: add `import numpy as np` to the imports. Replace `__init__`:

```python
    def __init__(self, db, matcher, encoder=None, default_threshold=0.35, default_top_k=10) -> None:
        self.db = db
        self.matcher = matcher
        self.encoder = encoder
        self.default_threshold = default_threshold
        self.default_top_k = default_top_k
        # ponytail: unbounded, ~3KB per distinct need; persist vectors behind an ANN index if this binds.
        self._need_vecs: dict[str, np.ndarray] = {}
```

Add module-level SQL after `MAX_LINKS_PER_NODE`:

```python
# Per (subscription, need): documents held and the weakest one's similarity.
_FLOORS_SQL = text("""
SELECT s.subscription_id, n.need,
       COALESCE(s.top_k, :default_top_k) AS top_k,
       COALESCE(s.threshold, :default_threshold) AS threshold,
       jsonb_array_length(COALESCE(s.bundle -> n.need, '[]'::jsonb)) AS docs,
       (SELECT min((m->>'similarity')::float)
          FROM jsonb_array_elements(COALESCE(s.bundle -> n.need, '[]'::jsonb)) m) AS min_sim
  FROM subscriptions s, unnest(s.needs) AS n(need)
 WHERE s.project_id = :project_id
""")
```

Replace the `_ids_admitting` stub and add helpers:

```python
    async def _floors(self, project_id) -> list[tuple[str, str, float]]:
        """(subscription_id, need, floor): the cosine a new node must reach to enter."""
        async with self.db.session() as session:
            rows = (await session.execute(_FLOORS_SQL, {
                "project_id": project_id,
                "default_top_k": self.default_top_k,
                "default_threshold": self.default_threshold,
            })).all()
        return [
            (r.subscription_id, r.need,
             r.threshold if r.docs < r.top_k or r.min_sim is None else max(r.threshold, r.min_sim))
            for r in rows
        ]

    def _need_vectors(self, needs) -> np.ndarray:
        missing = sorted({n for n in needs if n not in self._need_vecs})
        if missing:
            self._need_vecs.update(zip(missing, self.encoder.encode(missing)))
        return np.stack([self._need_vecs[n] for n in needs])

    async def _ids_admitting(self, project_id, keys) -> set[str] | None:
        """Subscriptions with a need some changed node reaches the floor of; None when unknown."""
        if self.encoder is None:
            return None
        async with self.db.session() as session:
            nodes = (await session.execute(
                select(Embedding.embedding)
                .where(Embedding.project_id == project_id)
                .where(Embedding.data_key.in_(keys))
            )).scalars().all()
        floors = await self._floors(project_id)
        if not nodes or not floors:
            return set()
        best = (np.stack(nodes) @ self._need_vectors([need for _, need, _ in floors]).T).max(axis=0)
        return {sub for (sub, _, floor), score in zip(floors, best) if score >= floor}
```

`src/core/context_engine.py`, replace the `SubscriptionService(...)` construction:

```python
        self.subscriptions = SubscriptionService(
            db, HybridMatcher(self.semantic_matcher),
            encoder=self.semantic_matcher.model,
            default_threshold=self.semantic_matcher.threshold,
            default_top_k=self.semantic_matcher.max_matches,
        )
```

The encoder failure test passes through Task 3's `except Exception` fallback in `reconcile_project`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_subscription_admission.py tests/test_subscription_affected.py tests/test_subscription_reconcile.py tests/test_context_engine.py -v`
Expected: PASS. Then the full suite: `pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add src/core/subscriptions.py src/core/context_engine.py tests/test_subscription_admission.py
git commit -m "Reconcile only subscriptions a new node can enter

Compares changed nodes against each need's admission floor: the
threshold until the bundle is full, then its weakest match. Floors come
from the stored bundle in SQL; need vectors are cached in memory."
```

---

### Task 5: Backstop sweep

**Files:**
- Create: `src/core/reconcile_sweep.py`
- Modify: `main.py` (lifespan)
- Modify: `.env.example` (document `RECONCILE_SWEEP_SECONDS`)
- Test: `tests/test_reconcile_sweep.py`

**Interfaces:**
- Consumes: `SubscriptionService.reconcile_project(project_id)`.
- Produces: `async sweep_once(db, subscriptions) -> bool` (False when another replica holds the lock), `async run_sweep(db, subscriptions, interval: float) -> None` (loops forever; cancel to stop).

- [ ] **Step 1: Write the failing tests** (`tests/test_reconcile_sweep.py`)

```python
import asyncio

import pytest

from src.core.reconcile_sweep import sweep_once
from src.core.subscriptions import SubscriptionService


class _Matcher:
    def __init__(self):
        self.value = 1
        self.gate = None

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        if self.gate is not None:
            await self.gate.wait()
        return {n: [{"data_key": "k", "document": "k", "similarity": 0.9, "data": {"v": self.value}}] for n in needs}


@pytest.mark.asyncio
async def test_sweep_reconciles_every_project(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    s1 = await svc.create("p1", ["a"])
    s2 = await svc.create("p2", ["a"])
    m.value = 2

    assert await sweep_once(db, svc) is True

    assert (await svc.get_bundle(s1))["a"][0]["data"] == {"v": 2}
    assert (await svc.get_bundle(s2))["a"][0]["data"] == {"v": 2}


@pytest.mark.asyncio
async def test_sweep_with_no_subscriptions(db):
    assert await sweep_once(db, SubscriptionService(db, _Matcher())) is True


@pytest.mark.asyncio
async def test_concurrent_sweeps_run_once(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    await svc.create("p1", ["a"])
    m.gate = asyncio.Event()

    first = asyncio.create_task(sweep_once(db, svc))
    await asyncio.sleep(0.2)  # first sweep holds the lock, blocked in match
    second = await sweep_once(db, svc)
    m.gate.set()

    assert second is False
    assert await first is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_reconcile_sweep.py -v`
Expected: FAIL (`ModuleNotFoundError: src.core.reconcile_sweep`).

- [ ] **Step 3: Implement**

`src/core/reconcile_sweep.py`:

```python
"""Periodic full reconcile: catches matches the publish-time filter misses."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from src.core.db_models import Subscription

logger = logging.getLogger(__name__)

# Arbitrary constant key shared by every replica.
_LOCK_KEY = 0x636F6E746578


async def sweep_once(db, subscriptions) -> bool:
    """Reconcile every project with subscriptions. False if another replica holds the sweep."""
    async with db.session() as lock_session:
        if not await lock_session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK_KEY}):
            return False
        projects = (await lock_session.execute(
            select(Subscription.project_id).distinct()
        )).scalars().all()
        for project_id in projects:
            try:
                await subscriptions.reconcile_project(project_id)
            except Exception:
                logger.exception("sweep reconcile failed for %s", project_id)
    return True


async def run_sweep(db, subscriptions, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await sweep_once(db, subscriptions)
        except Exception:
            logger.exception("reconcile sweep failed")
```

The lock is transaction-scoped on `lock_session`, held for the whole pass and released when the session closes.

`main.py`: add `from src.core.reconcile_sweep import run_sweep` with the other `src.core` imports. Inside `async with _mcp_server.session_manager.run():`, after `bridge_task = ...`, add:

```python
            sweep_seconds = float(os.getenv("RECONCILE_SWEEP_SECONDS", "300"))
            sweep_task = (
                asyncio.create_task(run_sweep(db, context_engine.subscriptions, sweep_seconds))
                if sweep_seconds > 0 else None
            )
```

and in its `finally`, before `bridge_task.cancel()`, cancel and await it the same way:

```python
                if sweep_task is not None:
                    sweep_task.cancel()
                    try:
                        await sweep_task
                    except asyncio.CancelledError:
                        pass
```

`.env.example`: add next to the other tuning vars:

```
RECONCILE_SWEEP_SECONDS=300         # Full subscription reconcile interval (catches hybrid-only matches); 0 disables
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_reconcile_sweep.py -v`, then `pytest tests/ -q`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/reconcile_sweep.py main.py .env.example tests/test_reconcile_sweep.py
git commit -m "Sweep all subscriptions periodically for eventual consistency

The publish-time filter is exact for vector matching only; hybrid can
admit a document below the cosine floor. One replica, chosen by an
advisory lock, runs a full reconcile every RECONCILE_SWEEP_SECONDS."
```
