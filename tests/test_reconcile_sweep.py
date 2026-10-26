import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from src.core.db_models import Embedding, Subscription
from src.core.reconcile_sweep import sweep_once
from src.core.subscriptions import SubscriptionService

WINDOW = {"max_age_seconds": 3600}


class _Matcher:
    def __init__(self):
        self.value = 1
        self.gate = None
        self.calls = []
        self.fail = False

    async def match_agent_needs(self, project_id, needs, top_k=None, threshold=None, since=None):
        self.calls.append(tuple(needs))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("search down")
        return {n: [{"data_key": f"{n}.k", "document": f"{n}.k", "similarity": 0.9, "data": {"v": self.value}}]
                for n in needs}


async def _node(db, project_id, key, age=timedelta(0)):
    async with db.session() as s:
        s.add(Embedding(project_id=project_id, data_key=key, node_key=key, data={}, embedding=[0.0] * 768,
                        created_at=datetime.now(timezone.utc) - age))


async def _age(db, project_id, key, age):
    async with db.session() as s:
        await s.execute(
            update(Embedding).where(Embedding.project_id == project_id).where(Embedding.node_key == key)
            .values(created_at=datetime.now(timezone.utc) - age)
        )
        await s.commit()


@pytest.mark.asyncio
async def test_full_sweep_reconciles_every_project(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    s1 = await svc.create("p1", ["a"])
    s2 = await svc.create("p2", ["a"])
    m.value = 2

    assert await sweep_once(db, svc, full=True) is True

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

    first = asyncio.create_task(sweep_once(db, svc, full=True))
    await asyncio.sleep(0.2)  # first sweep holds the lock, blocked in match
    second = await sweep_once(db, svc)
    m.gate.set()

    assert second is False
    assert await first is True


@pytest.mark.asyncio
@pytest.mark.parametrize("reconcile", [True, False])
async def test_sweep_purges_expired_subscriptions(db, reconcile):
    svc = SubscriptionService(db, _Matcher())
    live = await svc.create("p1", ["a"])
    stale = await svc.create("p1", ["b"])
    async with db.session() as session:
        await session.execute(
            update(Subscription).where(Subscription.subscription_id == stale)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()

    assert await sweep_once(db, svc, reconcile) is True

    assert await svc.all_ids() == [live]


@pytest.mark.asyncio
async def test_sweep_notifies_listeners_of_purged_subscriptions(db, notifier):
    svc = SubscriptionService(db, _Matcher())
    stale = await svc.create("p1", ["a"])
    queue = notifier.listen(stale)
    async with db.session() as session:
        await session.execute(
            update(Subscription).where(Subscription.subscription_id == stale)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        await session.commit()

    assert await sweep_once(db, svc, reconcile=False) is True

    assert await asyncio.wait_for(queue.get(), 2.0) == stale


@pytest.mark.asyncio
async def test_idle_sweep_rematches_nothing(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    for need in ("a", "b"):
        await _node(db, "p1", f"{need}.k")
    await svc.create("p1", ["a"], scope=WINDOW)
    await svc.create("p1", ["b"])
    m.calls.clear()

    assert await sweep_once(db, svc) is True

    assert m.calls == []


@pytest.mark.asyncio
async def test_sweep_rematches_only_windows_a_bundled_node_aged_out_of(db, notifier):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    for need in ("a", "b", "c"):
        await _node(db, "p1", f"{need}.k")
    aged = await svc.create("p1", ["a"], scope=WINDOW)
    await svc.create("p1", ["b"], scope=WINDOW)
    await svc.create("p1", ["c"])
    await _age(db, "p1", "a.k", timedelta(hours=2))
    await _age(db, "p1", "c.k", timedelta(hours=2))
    queue = notifier.listen(aged)
    m.calls.clear()
    m.value = 2

    await sweep_once(db, svc)

    assert m.calls == [("a",)]
    assert await asyncio.wait_for(queue.get(), 2.0) == aged


@pytest.mark.asyncio
async def test_sweep_rematches_a_window_whose_related_node_aged_out(db):
    m = _Matcher()
    real_match = m.match_agent_needs

    async def with_related(project_id, needs, **kw):
        bundle = await real_match(project_id, needs, **kw)
        for matches in bundle.values():
            matches[0]["related"] = [{"data_key": "a.k2", "similarity": 0.5, "data": {}}]
        return bundle
    m.match_agent_needs = with_related
    svc = SubscriptionService(db, m)
    await _node(db, "p1", "a.k")
    await _node(db, "p1", "a.k2")
    await svc.create("p1", ["a"], scope=WINDOW)
    await _age(db, "p1", "a.k2", timedelta(hours=2))
    m.calls.clear()

    await sweep_once(db, svc)

    assert m.calls == [("a",)]


@pytest.mark.asyncio
async def test_idle_sweep_skips_an_absolute_since_window(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    await _node(db, "p1", "a.k")
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await svc.create("p1", ["a"], scope={"since": since})
    m.calls.clear()

    await sweep_once(db, svc)

    assert m.calls == []


@pytest.mark.asyncio
async def test_sweep_rematches_a_window_whose_bundled_node_is_gone(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    await svc.create("p1", ["a"], scope=WINDOW)
    m.calls.clear()

    await sweep_once(db, svc)

    assert m.calls == [("a",)]


@pytest.mark.asyncio
async def test_sweep_catches_a_failed_publish_time_reconcile(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    await _node(db, "p1", "a.k")
    sub = await svc.create("p1", ["a"])
    m.fail, m.value = True, 2
    await svc.reconcile_project("p1", {"a.k"})
    m.fail = False

    await sweep_once(db, svc)
    assert (await svc.get_bundle(sub))["a"][0]["data"] == {"v": 2}

    m.calls.clear()
    await sweep_once(db, svc)
    assert m.calls == []


@pytest.mark.asyncio
async def test_sweep_catches_a_publish_time_reconcile_that_raised(db):
    m = _Matcher()
    svc = SubscriptionService(db, m)
    await _node(db, "p1", "a.k")
    await svc.create("p1", ["a"])

    async def unreachable(project_id, keys):
        raise OperationalError("SELECT", {}, Exception("connection lost"))
    svc._affected = unreachable
    with pytest.raises(OperationalError):
        await svc.reconcile_project("p1", {"a.k"})
    m.calls.clear()

    await sweep_once(db, svc)

    assert m.calls == [("a",)]


@pytest.mark.asyncio
async def test_sweep_rematches_everything_under_hybrid_search(db):
    m = _Matcher()
    m.hybrid_search = object()
    svc = SubscriptionService(db, m)
    await _node(db, "p1", "a.k")
    await svc.create("p1", ["a"])
    m.calls.clear()

    await sweep_once(db, svc)

    assert m.calls == [("a",)]

