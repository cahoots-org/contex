import asyncio

import pytest

from src.core.subscriptions import SubscriptionService


class _SlowMatcher:
    """Records the most matches in flight at once."""
    def __init__(self):
        self.running = 0
        self.peak = 0
        self.version = 0

    async def match_agent_needs(self, project_id, needs, top_k=None, threshold=None, since=None):
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0.05)
        self.running -= 1
        return {n: [{"data_key": f"k{self.version}", "similarity": 0.9, "data": {}, "description": "d"}] for n in needs}


@pytest.mark.asyncio
async def test_reconciles_run_at_most_the_concurrency_limit_and_leave_connections_free(db):
    matcher = _SlowMatcher()
    svc = SubscriptionService(db, matcher, reconcile_concurrency=2)
    projects = [f"p{i}" for i in range(20)]
    subs = [await svc.create(p, ["need"]) for p in projects]
    matcher.peak, matcher.version = 0, 1

    flood = asyncio.gather(*(svc.reconcile_project(p) for p in projects))
    await asyncio.sleep(0.01)
    assert await asyncio.wait_for(svc.project_of(subs[0]), 2) == "p0"
    assert db.engine.pool.checkedout() <= 2 * 2
    changed = await flood

    assert matcher.peak == 2
    assert sum(changed, []) == subs
