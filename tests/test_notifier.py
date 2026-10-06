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
