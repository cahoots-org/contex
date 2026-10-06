# Stage 1: Redis → Postgres LISTEN/NOTIFY Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Redis pub/sub with Postgres `LISTEN/NOTIFY` for subscription-update notifications and remove Redis from Contex entirely, with no behavior change.

**Architecture:** `reconcile_project` runs `pg_notify` inside the transaction that swaps a bundle, so a notification is delivered only once the bundle is readable. A `Notifier` in the API process holds one `LISTEN` connection (asyncpg) and fans notifications out to in-process queues consumed by the MCP bridge and the SSE stream. On reconnect it sends `RESYNC` so consumers re-read.

**Tech Stack:** Python 3.12, asyncpg 0.31 (already a dependency), SQLAlchemy async, pytest-asyncio, Postgres (ParadeDB image).

**Spec:** `docs/superpowers/specs/2026-10-05-async-ingest-and-gated-reconcile-design.md` (Stage 1 section)

## Global Constraints

- Work on branch `feat/listen-notify`, cut from `main`. One PR for the whole stage.
- Every commit passes the full suite. Run it with `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests` (local Postgres container `contex-postgres-1` is on port 5434).
- Channel name: `contex_subscription_updated`. Payload: JSON `{"subscription_id": str, "updated_at": ISO-8601 str}`.
- No new dependencies. asyncpg is already pinned (`asyncpg==0.31.0`).
- Imports at module top; no inline imports. Concise docstrings. No comments narrating the change.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01HSVMS4uK4G3oL7bAxmY2Ut
  ```

## Review Focus

1. **Bad or unreachable `LISTEN` DSN at startup** (wrong host, a transaction-mode pooler): `Notifier.start()` must raise so startup fails loudly, not run silently without notifications. Test in Task 1.
2. **Two SSE viewers on the same subscription:** both must receive each update. Test in Task 1.
3. **SSE client disconnects:** its queue must be removed so per-subscription listener sets don't grow without bound. Test in Task 1.
4. **Notification for a subscription nobody is listening to:** must be a no-op (no error, no state growth). Test in Task 1.
5. **Reconnect while listeners are held:** per-subscription routing must keep working for queues registered before the drop. Test in Task 1 (reconnect test sends a post-reconnect notify to a pre-existing per-subscription queue).

---

### Task 1: `Notifier` (LISTEN connection + in-process fan-out)

**Files:**
- Create: `src/core/notifier.py`
- Modify: `tests/conftest.py` (add `notifier` fixture)
- Test: `tests/test_notifier.py`

**Interfaces:**
- Produces:
  - `SUBSCRIPTION_UPDATED: str = "contex_subscription_updated"`
  - `RESYNC: str = "__resync__"`
  - `listen_dsn(db: DatabaseManager) -> str`
  - `class Notifier(dsn: str, *, keepalive: float = 30.0, max_backoff: float = 30.0)` with `async start()`, `async stop()`, `listen(subscription_id: str | None = None) -> asyncio.Queue`, `unlisten(queue) -> None`. Queues yield subscription id strings or `RESYNC`.
  - pytest fixture `notifier` (started `Notifier` on the test DB, `keepalive=0.1`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notifier.py`:

```python
"""Postgres LISTEN/NOTIFY fan-out for subscription updates."""
import asyncio
import json
import logging

import pytest
from sqlalchemy import text

from src.core.notifier import RESYNC, SUBSCRIPTION_UPDATED, Notifier


async def _notify(db, subscription_id, *, commit=True):
    async with db.session() as session:
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": SUBSCRIPTION_UPDATED,
             "payload": json.dumps({"subscription_id": subscription_id, "updated_at": "2026-10-05T00:00:00+00:00"})},
        )
        if commit:
            await session.commit()
        else:
            await session.rollback()


async def _next(queue, timeout=2.0):
    return await asyncio.wait_for(queue.get(), timeout)


async def _assert_empty(queue, wait=0.3):
    await asyncio.sleep(wait)
    assert queue.empty()


@pytest.mark.asyncio
async def test_committed_notify_reaches_all_and_matching_listeners(db, notifier):
    every = notifier.listen()
    mine = notifier.listen("sub_a")
    other = notifier.listen("sub_b")

    await _notify(db, "sub_a")

    assert await _next(every) == "sub_a"
    assert await _next(mine) == "sub_a"
    await _assert_empty(other)


@pytest.mark.asyncio
async def test_rolled_back_notify_is_not_delivered(db, notifier):
    every = notifier.listen()
    await _notify(db, "sub_a", commit=False)
    await _assert_empty(every)


@pytest.mark.asyncio
async def test_per_subscription_queue_coalesces(db, notifier):
    mine = notifier.listen("sub_a")
    await _notify(db, "sub_a")
    await _notify(db, "sub_a")
    assert await _next(mine) == "sub_a"
    await _assert_empty(mine)


@pytest.mark.asyncio
async def test_two_listeners_on_one_subscription_both_receive(db, notifier):
    first = notifier.listen("sub_a")
    second = notifier.listen("sub_a")
    await _notify(db, "sub_a")
    assert await _next(first) == "sub_a"
    assert await _next(second) == "sub_a"


@pytest.mark.asyncio
async def test_unlisten_stops_delivery_and_drops_empty_routes(db, notifier):
    mine = notifier.listen("sub_a")
    notifier.unlisten(mine)
    assert "sub_a" not in notifier._by_sub
    await _notify(db, "sub_a")
    await _assert_empty(mine)


@pytest.mark.asyncio
async def test_notify_for_unwatched_subscription_is_a_no_op(db, notifier):
    every = notifier.listen()
    await _notify(db, "sub_nobody")
    assert await _next(every) == "sub_nobody"
    assert notifier._by_sub == {}


@pytest.mark.asyncio
async def test_malformed_payload_is_dropped_with_warning(db, notifier, caplog):
    every = notifier.listen()
    with caplog.at_level(logging.WARNING):
        async with db.session() as session:
            await session.execute(
                text("SELECT pg_notify(:channel, 'not json')"), {"channel": SUBSCRIPTION_UPDATED}
            )
            await session.commit()
        await _assert_empty(every)
    assert "malformed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_reconnect_sends_resync_and_keeps_routing(db, notifier):
    every = notifier.listen()
    mine = notifier.listen("sub_a")
    pid = notifier._conn.get_server_pid()

    async with db.session() as session:
        await session.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        await session.commit()

    assert await _next(every, timeout=5) == RESYNC
    assert await _next(mine, timeout=5) == RESYNC

    await _notify(db, "sub_a")
    assert await _next(mine) == "sub_a"


@pytest.mark.asyncio
async def test_start_raises_when_listen_connection_fails():
    bad = Notifier("postgresql://contex:wrong@localhost:1/nope")
    with pytest.raises(Exception):
        await bad.start()
```

Add to `tests/conftest.py` (import at the top with the other `src.core` imports):

```python
from src.core.notifier import Notifier, listen_dsn
```

and the fixture, next to the `db` fixture:

```python
@pytest_asyncio.fixture
async def notifier(db):
    """A started Notifier on the test database; fast keepalive so drops are seen quickly."""
    n = Notifier(listen_dsn(db), keepalive=0.1)
    await n.start()
    yield n
    await n.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_notifier.py`
Expected: collection error, `ModuleNotFoundError: No module named 'src.core.notifier'`.

- [ ] **Step 3: Implement `src/core/notifier.py`**

```python
"""Subscription-update notifications over Postgres LISTEN/NOTIFY.

Reconcile runs ``pg_notify(SUBSCRIPTION_UPDATED, ...)`` in the transaction that
swaps a bundle, so a notification arrives only once the bundle is readable. One
Notifier per process holds the LISTEN connection and fans each notification out
to in-process listeners (the MCP bridge and SSE streams).

LISTEN needs a session-level connection, so the DSN must reach Postgres directly
or through a session-mode pooler, never a transaction-mode one.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

SUBSCRIPTION_UPDATED = "contex_subscription_updated"
# Sent to every listener after a reconnect: notifications may have been missed
# while disconnected, so listeners re-read everything they hold.
RESYNC = "__resync__"


def listen_dsn(db) -> str:
    """asyncpg DSN for the database behind ``db``."""
    return db.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)


def _offer(queue: asyncio.Queue, item: str) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass  # a pending item already means "re-read"


class Notifier:
    def __init__(self, dsn: str, *, keepalive: float = 30.0, max_backoff: float = 30.0) -> None:
        self._dsn = dsn
        self._keepalive = keepalive
        self._max_backoff = max_backoff
        self._all: set[asyncio.Queue] = set()
        self._by_sub: dict[str, set[asyncio.Queue]] = {}
        self._conn: Optional[asyncpg.Connection] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Open the LISTEN connection. Raises if it cannot be opened."""
        self._conn = await self._connect()
        self._task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._close()

    def listen(self, subscription_id: Optional[str] = None) -> asyncio.Queue:
        """A queue of updated subscription ids, or ``RESYNC``.

        With ``subscription_id``, only that subscription's updates arrive,
        coalesced to one pending item. Without it, every update arrives.
        """
        if subscription_id is None:
            queue: asyncio.Queue = asyncio.Queue()
            self._all.add(queue)
        else:
            queue = asyncio.Queue(maxsize=1)
            self._by_sub.setdefault(subscription_id, set()).add(queue)
        return queue

    def unlisten(self, queue: asyncio.Queue) -> None:
        self._all.discard(queue)
        for subscription_id in list(self._by_sub):
            self._by_sub[subscription_id].discard(queue)
            if not self._by_sub[subscription_id]:
                del self._by_sub[subscription_id]

    async def _connect(self) -> asyncpg.Connection:
        conn = await asyncpg.connect(self._dsn)
        await conn.add_listener(SUBSCRIPTION_UPDATED, self._on_notify)
        return conn

    async def _close(self) -> None:
        if self._conn is not None and not self._conn.is_closed():
            try:
                await self._conn.close(timeout=1)
            except Exception:
                self._conn.terminate()
        self._conn = None

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        try:
            subscription_id = json.loads(payload)["subscription_id"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Dropped a malformed subscription-updated notification: %s", exc)
            return
        for queue in self._all:
            queue.put_nowait(subscription_id)
        for queue in self._by_sub.get(subscription_id, ()):
            _offer(queue, subscription_id)

    def _broadcast_resync(self) -> None:
        for queue in self._all:
            queue.put_nowait(RESYNC)
        for queues in self._by_sub.values():
            for queue in queues:
                _offer(queue, RESYNC)

    async def _supervise(self) -> None:
        while True:
            try:
                while True:  # a dead connection fails the ping
                    await asyncio.sleep(self._keepalive)
                    await asyncio.wait_for(self._conn.execute("SELECT 1"), self._keepalive)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("LISTEN connection lost, reconnecting: %s", exc)
            await self._reconnect()
            self._broadcast_resync()

    async def _reconnect(self) -> None:
        backoff = 1.0
        while True:
            await self._close()
            try:
                self._conn = await self._connect()
                logger.info("LISTEN connection restored")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("LISTEN reconnect failed, retrying in %.0fs: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_notifier.py`
Expected: 9 passed.

- [ ] **Step 5: Run the full suite, then commit**

```bash
git add src/core/notifier.py tests/test_notifier.py tests/conftest.py
git commit -m "feat(notify): Postgres LISTEN/NOTIFY notifier for subscription updates

One LISTEN connection per process, fanned out to in-process queues:
every update, or one subscription's updates coalesced. Reconnects with
backoff after a failed keepalive and sends RESYNC so listeners re-read."
```

---

### Task 2: Reconcile emits `pg_notify` in the bundle-swap transaction

Redis publishing stays for now (removed in Task 5), so nothing downstream breaks.

**Files:**
- Modify: `src/core/subscriptions.py` (`reconcile_project`, the bundle-swap block around the `row.bundle = new_bundle` lines; imports)
- Test: `tests/test_subscription_reconcile.py`

**Interfaces:**
- Consumes: `SUBSCRIPTION_UPDATED` from Task 1; `notifier` fixture.
- Produces: every changed bundle commits together with `pg_notify('contex_subscription_updated', '{"subscription_id": ..., "updated_at": ...}')`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_subscription_reconcile.py` (add `import asyncio` at the top):

```python
@pytest.mark.asyncio
async def test_reconcile_notifies_changed_subscription_over_postgres(db, redis, notifier):
    m = _MutableMatcher([{"data_key": "cfg", "similarity": 0.9, "data": {"v": 1}, "description": "d"}])
    svc = SubscriptionService(db, m, redis)
    sub_id = await svc.create("p1", ["auth"])
    queue = notifier.listen(sub_id)

    m._bundle = [{"data_key": "cfg", "similarity": 0.95, "data": {"v": 2}, "description": "d"}]
    await svc.reconcile_project("p1")

    assert await asyncio.wait_for(queue.get(), 2) == sub_id


@pytest.mark.asyncio
async def test_unchanged_reconcile_sends_no_postgres_notification(db, redis, notifier):
    m = _MutableMatcher([{"data_key": "cfg", "similarity": 0.9, "data": {"v": 1}, "description": "d"}])
    svc = SubscriptionService(db, m, redis)
    sub_id = await svc.create("p1", ["auth"])
    queue = notifier.listen(sub_id)

    await svc.reconcile_project("p1")

    await asyncio.sleep(0.3)
    assert queue.empty()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_subscription_reconcile.py -k postgres`
Expected: `test_reconcile_notifies_changed_subscription_over_postgres` FAILS with `TimeoutError`; the unchanged test passes.

- [ ] **Step 3: Implement**

In `src/core/subscriptions.py`, change `from sqlalchemy import select` to `from sqlalchemy import select, text` and add `from src.core.notifier import SUBSCRIPTION_UPDATED`. Replace the bundle-swap block in `reconcile_project`:

```python
                now = datetime.now(timezone.utc)  # single timestamp for both DB + event
                async with self.db.session() as session:  # buffer-until-complete: one atomic swap
                    row = (await session.execute(
                        select(Subscription).where(Subscription.subscription_id == sub.subscription_id)
                    )).scalar_one()
                    row.bundle = new_bundle
                    row.bundle_updated_at = now
                    await session.commit()  # commit BEFORE publish: reader must see committed value
                await self.redis.publish(
```

with:

```python
                now = datetime.now(timezone.utc)  # single timestamp for both DB + event
                event = json.dumps({
                    "subscription_id": sub.subscription_id,
                    "updated_at": now.isoformat(),
                })
                async with self.db.session() as session:  # buffer-until-complete: one atomic swap
                    row = (await session.execute(
                        select(Subscription).where(Subscription.subscription_id == sub.subscription_id)
                    )).scalar_one()
                    row.bundle = new_bundle
                    row.bundle_updated_at = now
                    # Delivered only on commit, so listeners never see an unreadable bundle.
                    await session.execute(
                        text("SELECT pg_notify(:channel, :payload)"),
                        {"channel": SUBSCRIPTION_UPDATED, "payload": event},
                    )
                    await session.commit()
                await self.redis.publish(
```

Leave the existing `self.redis.publish(...)` call below it unchanged for now.

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_subscription_reconcile.py`
Expected: all pass.

- [ ] **Step 5: Run the full suite, then commit**

```bash
git add src/core/subscriptions.py tests/test_subscription_reconcile.py
git commit -m "feat(notify): emit pg_notify when reconcile swaps a bundle

The notification is sent inside the swap transaction, so it is delivered
only on commit. Redis publishing remains until consumers move over."
```

---

### Task 3: MCP bridge consumes the `Notifier`

**Files:**
- Modify: `src/core/mcp_bridge.py` (rewrite)
- Modify: `src/core/subscriptions.py` (add `all_ids`)
- Modify: `main.py` (start/stop the notifier; run the bridge on it)
- Test: `tests/test_mcp_bridge.py` (rewrite), `tests/test_subscription_service.py` (add `all_ids` test)

**Interfaces:**
- Consumes: `Notifier`, `listen_dsn`, `RESYNC` from Task 1.
- Produces:
  - `SubscriptionService.all_ids() -> list[str]`
  - `run_bridge(notifier: Notifier, bus, subscription_ids: Callable[[], Awaitable[list[str]]]) -> None` (runs until cancelled)
  - `resource_uri_for(subscription_id: str) -> str` (unchanged)
  - `app.state.notifier` (the process's started `Notifier`)

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_mcp_bridge.py` entirely:

```python
import asyncio
import json

import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated
from sqlalchemy import text

from src.core.mcp_bridge import resource_uri_for, run_bridge
from src.core.notifier import SUBSCRIPTION_UPDATED


def test_resource_uri_for():
    assert resource_uri_for("sub_abc") == "contex://subscriptions/sub_abc"


async def _wait_for_uris(seen, expected, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if expected <= {ev.uri for ev in seen if isinstance(ev, ResourceUpdated)}:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"expected {expected}, saw {seen}")


@pytest.mark.asyncio
async def test_bridge_pushes_resource_updated_on_notification(db, notifier):
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))

    async def no_ids():
        return []

    task = asyncio.create_task(run_bridge(notifier, bus, no_ids))
    try:
        await asyncio.sleep(0.05)
        async with db.session() as session:
            await session.execute(
                text("SELECT pg_notify(:c, :p)"),
                {"c": SUBSCRIPTION_UPDATED,
                 "p": json.dumps({"subscription_id": "sub_x", "updated_at": "2026-10-05T00:00:00+00:00"})},
            )
            await session.commit()
        await _wait_for_uris(seen, {"contex://subscriptions/sub_x"})
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_bridge_pushes_every_subscription_on_resync(notifier):
    bus = InMemorySubscriptionBus()
    seen = []
    bus.subscribe(lambda ev: seen.append(ev))

    async def ids():
        return ["sub_a", "sub_b"]

    task = asyncio.create_task(run_bridge(notifier, bus, ids))
    try:
        await asyncio.sleep(0.05)
        notifier._broadcast_resync()
        await _wait_for_uris(seen, {"contex://subscriptions/sub_a", "contex://subscriptions/sub_b"})
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_bridge_stops_listening_when_cancelled(notifier):
    bus = InMemorySubscriptionBus()

    async def no_ids():
        return []

    task = asyncio.create_task(run_bridge(notifier, bus, no_ids))
    await asyncio.sleep(0.05)
    assert len(notifier._all) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert notifier._all == set()
```

Append to `tests/test_subscription_service.py`:

```python
@pytest.mark.asyncio
async def test_all_ids_lists_every_subscription(db, redis):
    svc = SubscriptionService(db, _StubMatcher(), redis)
    a = await svc.create("p1", ["x"])
    b = await svc.create("p2", ["y"])
    assert sorted(await svc.all_ids()) == sorted([a, b])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_mcp_bridge.py tests/test_subscription_service.py`
Expected: FAIL (`run_bridge` signature mismatch; `all_ids` missing).

- [ ] **Step 3: Implement**

Add to `SubscriptionService` in `src/core/subscriptions.py`, after `get_bundle`:

```python
    async def all_ids(self) -> list[str]:
        async with self.db.session() as session:
            return list((await session.execute(select(Subscription.subscription_id))).scalars())
```

Replace `src/core/mcp_bridge.py` entirely:

```python
"""Bridges subscription-updated notifications into MCP resources/updated pushes.
The second of two modules allowed to import the mcp SDK."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from mcp.server.subscriptions import ResourceUpdated

from src.core.notifier import RESYNC, Notifier

logger = logging.getLogger(__name__)

_RESOURCE_URI_PREFIX = "contex://subscriptions/"


def resource_uri_for(subscription_id: str) -> str:
    return f"{_RESOURCE_URI_PREFIX}{subscription_id}"


async def run_bridge(
    notifier: Notifier, bus, subscription_ids: Callable[[], Awaitable[list[str]]]
) -> None:
    """Push resources/updated for each update; after RESYNC, for every subscription.
    Runs until cancelled."""
    queue = notifier.listen()
    try:
        while True:
            item = await queue.get()
            try:
                ids = await subscription_ids() if item == RESYNC else [item]
                for subscription_id in ids:
                    await bus.publish(ResourceUpdated(uri=resource_uri_for(subscription_id)))
            except Exception:
                logger.exception("MCP bridge failed to push an update; continuing")
    finally:
        notifier.unlisten(queue)
```

In `main.py`:
- Add the import `from src.core.notifier import Notifier, listen_dsn` with the other `src.core` imports.
- Right after the "Database schema migrated to head" block (before the Redis connect block), add:

```python
    # LISTEN connection for subscription-update notifications.
    notifier = Notifier(listen_dsn(db))
    try:
        await notifier.start()
        logger.info("Notification listener connected")
    except Exception as e:
        logger.error("Failed to open the notification LISTEN connection", error=str(e))
        raise
    app.state.notifier = notifier
```

- Replace the bridge block:

```python
        async with _mcp_server.session_manager.run():
            mcp_stop = asyncio.Event()
            bridge_task = asyncio.create_task(run_bridge(redis, _mcp_bus, mcp_stop))
            try:
                yield
            finally:
                mcp_stop.set()
                bridge_task.cancel()
                try:
                    await bridge_task
                except asyncio.CancelledError:
                    pass
```

with:

```python
        async with _mcp_server.session_manager.run():
            bridge_task = asyncio.create_task(
                run_bridge(notifier, _mcp_bus, context_engine.subscriptions.all_ids)
            )
            try:
                yield
            finally:
                bridge_task.cancel()
                try:
                    await bridge_task
                except asyncio.CancelledError:
                    pass
```

- In the lifespan's outer `finally` (just before `await shutdown_cleanup(app.state)`), add `await notifier.stop()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_mcp_bridge.py tests/test_subscription_service.py tests/test_mcp_mount.py`
Expected: all pass.

- [ ] **Step 5: Port the end-to-end MCP live-update test**

In `tests/test_mcp_live_update_e2e.py`, replace the Redis pubsub capture with the notifier and drive the bridge through a real `run_bridge` task. Replace the body after `bus.subscribe(...)` with:

```python
    bridge = asyncio.create_task(run_bridge(notifier, bus, engine.subscriptions.all_ids))
    try:
        await asyncio.sleep(0.05)
        await engine.publish_data(DataPublishEvent(
            project_id="p", data_key="db",
            data={"purpose": "database connection settings"}, data_format="json",
        ))
        deadline = asyncio.get_running_loop().time() + 5
        while not any(isinstance(ev, ResourceUpdated) and ev.uri == uri for ev in updated):
            assert asyncio.get_running_loop().time() < deadline, "no resources/updated push"
            await asyncio.sleep(0.05)
    finally:
        bridge.cancel()

    contents = list(await server.read_resource(uri))
    bundle = json.loads(contents[0].content)
    assert any(
        m["data_key"].startswith("db.") or m["data_key"] == "db"
        for matches in bundle.values()
        for m in matches
    )
```

Change the test signature to `(db, redis, notifier)`, add `import asyncio`, and change the bridge import to `from src.core.mcp_bridge import resource_uri_for, run_bridge`.

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_mcp_live_update_e2e.py`
Expected: PASS.

- [ ] **Step 6: Run the full suite, then commit**

```bash
git add src/core/mcp_bridge.py src/core/subscriptions.py main.py tests/test_mcp_bridge.py tests/test_subscription_service.py tests/test_mcp_live_update_e2e.py
git commit -m "feat(notify): drive MCP resources/updated from the Postgres notifier

The bridge listens on the process Notifier instead of Redis, and after a
reconnect pushes an update for every subscription so clients re-read."
```

---

### Task 4: SSE stream consumes the `Notifier`

**Files:**
- Modify: `src/core/context_engine.py` (`__init__`: add `notifier` keyword)
- Modify: `src/web/live.py` (rewrite the loop)
- Modify: `main.py` (pass `notifier=notifier` to `ContextEngine`)
- Test: `tests/test_sandbox_live.py`

**Interfaces:**
- Consumes: `Notifier.listen(subscription_id)`, `Notifier.unlisten(queue)`.
- Produces: `ContextEngine(db, redis, ..., notifier: Notifier | None = None)`; `engine.notifier`.

- [ ] **Step 1: Update the tests**

In `tests/test_sandbox_live.py`:
- In `test_stream_yields_initial_then_updates_then_cleans_up`, change the signature to `(db, redis, notifier)` and construct `ContextEngine(db=db, redis=redis, notifier=notifier, similarity_threshold=0.1, max_matches=10)`.
- Replace `test_stream_cleans_up_subscription_on_pubsub_setup_failure` with:

```python
@pytest.mark.asyncio
async def test_stream_cleans_up_subscription_when_setup_fails(db, redis, notifier, monkeypatch):
    """If reading the first bundle raises after the subscription is created, the
    subscription is still deleted and the listener released."""
    engine = ContextEngine(db=db, redis=redis, notifier=notifier, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    monkeypatch.setattr(engine.subscriptions, "get_bundle", AsyncMock(side_effect=RuntimeError("boom")))

    agen = stream_subscription_updates(engine, "q", "auth token secret", top_k=10, threshold=0.1)
    with pytest.raises(RuntimeError, match="boom"):
        await agen.__anext__()

    async with db.session() as session:
        count = (await session.execute(
            select(func.count()).select_from(Subscription).where(Subscription.project_id == "q")
        )).scalar_one()
    assert count == 0, "Subscription was orphaned after setup failure"
    assert notifier._by_sub == {}
```

Remove the now-unused `MagicMock` import if nothing else uses it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_sandbox_live.py`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'notifier'`.

- [ ] **Step 3: Implement**

In `src/core/context_engine.py`, add `from .notifier import Notifier` with the other relative imports, add the parameter `notifier: Optional[Notifier] = None,` after `embed_model`, and set `self.notifier = notifier` next to `self.redis = redis`.

Replace the body of `stream_subscription_updates` in `src/web/live.py` (keep its signature):

```python
    """Create an ephemeral subscription for `need`, stream its bundle, and re-stream
    it on every update notification. The subscription is always deleted when the
    stream closes."""
    sub_id = None
    queue = None
    try:
        sub_id = await engine.subscriptions.create(project_id, [need], top_k=top_k, threshold=threshold)
        queue = engine.notifier.listen(sub_id)

        # Read AFTER listening so a change racing the create() is not missed.
        bundle = await engine.subscriptions.get_bundle(sub_id)
        yield _sse({"type": "bundle", "bundle": bundle, "updated_at": None})

        while True:
            await queue.get()  # an update or RESYNC: either way, re-read
            bundle = await engine.subscriptions.get_bundle(sub_id)
            yield _sse({
                "type": "bundle",
                "bundle": bundle,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
    finally:
        try:
            if queue is not None:
                engine.notifier.unlisten(queue)
        finally:
            if sub_id is not None:
                await engine.subscriptions.delete(sub_id)
```

In `main.py`, add `notifier=notifier,` to the `ContextEngine(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests/test_sandbox_live.py tests/test_tenant_isolation.py`
Expected: all pass.

- [ ] **Step 5: Run the full suite, then commit**

```bash
git add src/core/context_engine.py src/web/live.py main.py tests/test_sandbox_live.py
git commit -m "feat(notify): stream sandbox SSE updates from the Postgres notifier"
```

---

### Task 5: Remove Redis from the code

Nothing reads Redis any more. Remove the publisher, the client, and every hook.

**Files:**
- Delete: `src/core/pubsub.py`
- Modify: `src/core/subscriptions.py`, `src/core/context_engine.py`, `main.py`, `src/core/graceful_shutdown.py`, `src/core/tracing.py`, `src/core/sentry_integration.py`, `src/core/mcp_adapter.py:116` (comment), `requirements.txt`, `tests/conftest.py`, `tests/test_tracing.py`, every test file that takes the `redis` fixture.

**Interfaces:**
- Produces: `SubscriptionService(db, matcher)`; `ContextEngine(db, ..., notifier=None)` with no `redis` parameter.

- [ ] **Step 1: Remove the Redis publisher and parameters**

`src/core/subscriptions.py`:
- `__init__(self, db, matcher, redis)` → `__init__(self, db, matcher)`; delete `self.redis = redis`.
- Delete the `await self.redis.publish(...)` call (and its `json.dumps({...})` argument) that follows the swap transaction in `reconcile_project`.

`src/core/context_engine.py`:
- Delete `from redis.asyncio import Redis`, the `redis: Redis,` parameter, and `self.redis = redis`.
- `SubscriptionService(db, HybridMatcher(self.semantic_matcher), redis)` → `SubscriptionService(db, HybridMatcher(self.semantic_matcher))`.
- Docstring line `Real-time notifications: Redis pub/sub` → `Real-time notifications: Postgres LISTEN/NOTIFY`.

`main.py`:
- Delete `from src.core.pubsub import create_redis_connection`, `REDIS_MODE = ...`, the `redis_mode=REDIS_MODE,` log field, the "Connect to Redis for pub/sub" try/except block, `redis=redis,` in `ContextEngine(...)`, and `app.state.redis = redis`.
- Replace the "Instrument Redis with tracing" block with:

```python
    app.state.tracing_manager = get_tracing_manager()
```

  and add `from src.core.tracing import get_tracing_manager` to the module imports (the old block imported it inline).
- Update the docstring at line 54 (`database/Redis`) to `database`.

`src/core/graceful_shutdown.py`: delete `drain_connections` and the "Close Redis connection" block in `shutdown_cleanup`.

`src/core/tracing.py`: delete the `RedisInstrumentor` import, the `instrument_redis` method, and "and Redis" from the module docstring line. In `tests/test_tracing.py`, delete `test_instrument_redis`.

`src/core/sentry_integration.py`: delete the `RedisIntegration` import and the "Add Redis integration if available" block.

`src/core/mcp_adapter.py:116`: reword the comment ending in "Redis events." to say "Postgres notifications." (read the surrounding comment and keep its meaning).

`requirements.txt`: delete the `# Redis (for pub/sub only)` line, `redis==8.1.0`, `opentelemetry-instrumentation-redis==0.66b0`, and `fakeredis==2.38.0`. On the JWT pin comment, drop ` and redis[jwt]`.

Delete `src/core/pubsub.py`.

- [ ] **Step 2: Remove Redis from the tests**

In `tests/conftest.py`, delete `from fakeredis import FakeAsyncRedis` and the `redis` and `mock_redis` fixtures.

Run this script from the repo root to strip the fixture from every test:

```bash
python - <<'EOF'
import pathlib, re
for path in pathlib.Path("tests").glob("**/*.py"):
    src = path.read_text()
    out = re.sub(r"^\s*redis=redis,\s*\n", "", src, flags=re.M)
    out = out.replace("redis=redis, ", "")
    out = re.sub(r"(SubscriptionService\([^\n]*?), redis\)", r"\1)", out)
    out = re.sub(r"(def \w+\([^)]*?), redis\b", r"\1", out)
    out = re.sub(r"(def \w+\()redis, ", r"\1", out)
    if out != src:
        path.write_text(out)
        print("updated", path)
EOF
```

Then confirm nothing is left:

Run: `grep -rn "redis" tests src main.py requirements.txt --include=*.py --include=*.txt`
Expected: no output. Fix any remaining hit by hand (for example a fixture defined as `async def context_engine(self, db, redis)` on its own line).

- [ ] **Step 3: Run the full suite**

Run: `DATABASE_URL="postgresql+asyncpg://contex:contex_password@localhost:5434/contex_test" python -m pytest -q tests`
Expected: all pass, with no Redis server running. Verify by stopping it first: `docker stop contex-redis-1`, run the suite, then `docker start contex-redis-1` only if you need it for something else.

- [ ] **Step 4: Commit**

```bash
git add -A src main.py requirements.txt tests
git commit -m "refactor: remove Redis

Subscription updates now travel over Postgres LISTEN/NOTIFY, so the Redis
client, publisher, Sentinel config, tracing and Sentry hooks, and the
fakeredis test fixture go."
```

---

### Task 6: Remove Redis from infrastructure, CI, and docs

**Files:**
- Modify: `docker-compose.yml`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/ISSUE_TEMPLATE/bug_report.yml`, `.env.example`, `.gitignore`, `README.md`, `CONTRIBUTING.md`, `docs/DATABASE.md`, `docs/RUNBOOKS.md`, `docs/METRICS.md`, `docs/LOGGING.md`, `grafana/README.md`, `grafana/contex-dashboard.json`, `grafana/dashboards/contex-overview.json`, `grafana/dashboards/contex-reliability.json`, `grafana/contex-tempo-dashboard.json`

- [ ] **Step 1: Compose, CI, env**

- `docker-compose.yml`: delete the `redis:` service, the `REDIS_URL=redis://redis:6379` environment line, and the `redis:` entry (with its `condition: service_healthy`) under `contex.depends_on`.
- `.github/workflows/ci.yml` and `release.yml`: delete the `redis:` service block and the `REDIS_URL:` env line.
- `.github/ISSUE_TEMPLATE/bug_report.yml`: delete the `redis-version` input block.
- `.env.example`: delete the whole Redis section (the `# Redis (pub/sub notifications)` header through `# REDIS_SENTINEL_PASSWORD=`, including its separator lines). Below the `DATABASE_URL` line add:

```
# LISTEN/NOTIFY (live subscription updates) needs a session-level connection:
# point DATABASE_URL at Postgres directly or a session-mode pooler, never a
# transaction-mode pooler.
```

- `.gitignore`: delete the `# Redis` comment and the entry under it.

- [ ] **Step 2: Docs**

- `README.md:156`: `docker compose up -d      # Contex, ParadeDB, Redis` → `docker compose up -d      # Contex, ParadeDB`. Leave line 147 (Redis as a future connector *source*) unchanged.
- `CONTRIBUTING.md`: delete the Redis prerequisite line, the Redis container steps, the `REDIS_URL` export, the "Working with Redis (Pub/Sub)" section, and the Redis version line; change `docker compose up -d postgres redis` to `docker compose up -d postgres`; in the example conftest snippet, delete the `redis` fixture and change `context_engine(db, redis)` / `ContextEngine(db=db, redis=redis)` to `context_engine(db)` / `ContextEngine(db=db)`.
- `docs/DATABASE.md`: delete the "Redis - Pub/sub notifications only" bullet and the `redis:` compose snippet; add a bullet "Live subscription updates use Postgres `LISTEN/NOTIFY` (channel `contex_subscription_updated`); the listener needs a session-level connection." Leave the historical `notification_method ... 'redis'` schema line as is (it documents a migration column).
- `docs/RUNBOOKS.md`: delete the "Redis Operations" section and its table-of-contents entry, the `redis-cli ping` health lines, "If Redis is down" bullet, and Redis password rotation items. Add under the health checks: "Live updates: confirm the listener with `SELECT pid, query FROM pg_stat_activity WHERE query LIKE 'LISTEN%';`."
- `docs/METRICS.md`: delete the `contex_redis_operation_duration_seconds` and `contex_redis_connections` entries (neither metric exists in `src/core/metrics.py`).
- `docs/LOGGING.md:188`: change the example to `logger.critical("Database connection lost")`.

- [ ] **Step 3: Grafana**

Remove panels for metrics and spans that no longer exist:

```bash
python - <<'EOF'
import json, pathlib
files = ["grafana/contex-dashboard.json", "grafana/dashboards/contex-overview.json",
         "grafana/dashboards/contex-reliability.json", "grafana/contex-tempo-dashboard.json"]
def keep(panel):
    return "redis" not in json.dumps(panel).lower()
def prune(node):
    if isinstance(node, dict):
        for key in ("panels", "rules", "alerts"):
            if isinstance(node.get(key), list):
                node[key] = [prune(p) for p in node[key] if keep(p)]
        return {k: prune(v) if k not in ("panels", "rules", "alerts") else v for k, v in node.items()}
    if isinstance(node, list):
        return [prune(x) for x in node]
    return node
for f in files:
    p = pathlib.Path(f)
    p.write_text(json.dumps(prune(json.loads(p.read_text())), indent=2) + "\n")
EOF
grep -il redis grafana/*.json grafana/dashboards/*.json
```

Expected: no output from the grep. In `grafana/README.md`, delete the Redis bullets (operations, pool exhausted, `contex_redis_connections`, pool alert).

- [ ] **Step 4: Verify nothing is left and the stack starts**

Run: `grep -rni redis --exclude-dir=.git --exclude-dir=docs/superpowers --exclude-dir=docs/audits --exclude-dir=.superpowers --exclude-dir=.pytest_cache --exclude-dir=htmlcov . | grep -v "README.md:147\|DATABASE.md.*notification_method\|001_initial_schema.py"`
Expected: no output (ignore `.env` and `.claude/settings.local.json`, which are local and untracked).

Run: `docker compose up -d --build && sleep 20 && curl -sf localhost:8001/health && docker compose logs contex | grep -i "notification listener connected"`
Expected: health returns OK and the log line appears. Then `docker compose down`.

- [ ] **Step 5: Commit**

```bash
git add -A docker-compose.yml .github .env.example .gitignore README.md CONTRIBUTING.md docs/DATABASE.md docs/RUNBOOKS.md docs/METRICS.md docs/LOGGING.md grafana
git commit -m "chore: drop Redis from compose, CI, docs and dashboards"
```

---

### Task 7: Open the PR

- [ ] **Step 1: Push and open**

```bash
git push -u origin feat/listen-notify
gh pr create --title "Replace Redis with Postgres LISTEN/NOTIFY" --body "$(cat <<'EOF'
Stage 1 of docs/superpowers/specs/2026-10-05-async-ingest-and-gated-reconcile-design.md.

Subscription-update notifications move from Redis pub/sub to Postgres LISTEN/NOTIFY, and Redis is removed. No behavior change.

- `pg_notify` runs inside the bundle-swap transaction, so a notification is delivered only on commit.
- `Notifier` holds one LISTEN connection per process and fans out to the MCP bridge and SSE streams. On reconnect it sends RESYNC; the bridge pushes every subscription and SSE re-reads.
- Postgres is now the only infrastructure dependency.

Deploy note: the listener needs a session-level connection (direct or session-mode pooler). An existing Redis service and `REDIS_*` variables can be deleted after this deploys.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01HSVMS4uK4G3oL7bAxmY2Ut
EOF
)"
```
