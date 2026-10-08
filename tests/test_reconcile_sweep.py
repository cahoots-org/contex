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
