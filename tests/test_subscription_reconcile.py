# tests/test_subscription_reconcile.py
import json
import pytest
from src.core.subscriptions import SubscriptionService


class _MutableMatcher:
    """Returns whatever bundle it's currently told to."""
    def __init__(self, bundle):
        self._bundle = bundle

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None):
        return {n: self._bundle for n in needs}


class _KeyedMatcher:
    """Deterministic matcher: each need has its own independent list of matches."""
    def __init__(self, bundles):  # bundles: dict[need -> list]
        self.bundles = bundles

    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None):
        return {n: list(self.bundles.get(n, [])) for n in needs}


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


@pytest.mark.asyncio
async def test_reconcile_only_changed_subscription_is_updated(db, redis):
    """Only the subscription whose bundle changed is returned in changed ids."""
    initial_a = [{"data_key": "ak", "similarity": 0.7, "data": {"src": "a"}, "description": "da"}]
    initial_b = [{"data_key": "bk", "similarity": 0.6, "data": {"src": "b"}, "description": "db"}]
    matcher = _KeyedMatcher({"a": list(initial_a), "b": list(initial_b)})
    svc = SubscriptionService(db, matcher, redis)

    sub_a = await svc.create("p1", ["a"])
    sub_b = await svc.create("p1", ["b"])

    # mutate only the "a" need's bundle
    new_a = [{"data_key": "ak", "similarity": 0.99, "data": {"src": "a-updated"}, "description": "da"}]
    matcher.bundles["a"] = new_a

    changed = await svc.reconcile_project("p1")

    # only sub_a changed
    assert sub_a in changed
    assert sub_b not in changed
    assert len(changed) == 1

    # sub_a now reflects the new value
    bundle_a = await svc.get_bundle(sub_a)
    assert bundle_a["a"][0]["data"] == {"src": "a-updated"}

    # sub_b is untouched
    bundle_b = await svc.get_bundle(sub_b)
    assert bundle_b["b"][0]["data"] == {"src": "b"}


@pytest.mark.asyncio
async def test_reconcile_scoped_to_project(db, redis):
    """reconcile_project("p1") must not touch subscriptions in other projects."""
    # "other" project matcher always returns a different bundle so that, if reconcile
    # mistakenly crosses project boundaries, the bundle WOULD change.
    other_initial = [{"data_key": "ok", "similarity": 0.5, "data": {"v": 0}, "description": None}]
    other_new = [{"data_key": "ok", "similarity": 0.5, "data": {"v": 999}, "description": None}]

    class _DifferentPerCall:
        def __init__(self):
            self._call_count = 0
            self.bundles = {"need_o": list(other_initial)}

        async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None):
            self._call_count += 1
            # Always return a "different" bundle so any cross-project reconcile would be detected
            return {n: [{"data_key": "ok", "similarity": 0.5, "data": {"v": self._call_count}, "description": None}] for n in needs}

    other_matcher = _DifferentPerCall()
    svc_other = SubscriptionService(db, other_matcher, redis)
    sub_other = await svc_other.create("other", ["need_o"])

    # capture the initial bundle stored at creation time
    bundle_before = await svc_other.get_bundle(sub_other)

    # now reconcile a completely different project using a matcher that returns different data
    p1_matcher = _KeyedMatcher({"need_p1": [{"data_key": "pk", "similarity": 0.9, "data": {"v": 42}, "description": None}]})
    svc_p1 = SubscriptionService(db, p1_matcher, redis)
    changed = await svc_p1.reconcile_project("p1")

    # the "other" project subscription must not appear in changed ids
    assert sub_other not in changed

    # the "other" subscription's stored bundle must be unchanged
    bundle_after = await svc_other.get_bundle(sub_other)
    assert bundle_after == bundle_before
