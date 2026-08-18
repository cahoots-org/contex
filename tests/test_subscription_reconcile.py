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

    pubsub = redis.pubsub()
    await pubsub.subscribe(f"subscription:{sub_id}:updated")

    # matcher returns the same bundle -> no change
    changed = await svc.reconcile_project("p1")
    assert changed == []
    # no updated event should have been published for the unchanged subscription
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
    assert msg is None
