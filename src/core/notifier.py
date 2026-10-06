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
