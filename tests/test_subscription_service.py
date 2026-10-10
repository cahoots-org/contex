import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.core.db_models import Subscription
from src.core.subscriptions import SubscriptionService, _content_id
from src.core.tenant import DEFAULT_TENANT_ID


class _StubMatcher:
    async def match(self, project_id, needs, metadata=None, top_k=None, threshold=None, since=None):
        return {n: [{"data_key": "cfg", "similarity": 0.9, "data": {"x": 1}, "description": "auth"}] for n in needs}


@pytest.mark.asyncio
async def test_create_materializes_bundle_and_get_bundle_reads_it(db):
    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("p1", ["auth config"])
    assert sub_id.startswith("sub_")

    bundle = await svc.get_bundle(sub_id)
    assert bundle["auth config"][0]["data_key"] == "cfg"


@pytest.mark.asyncio
async def test_get_bundle_unknown_raises(db):
    svc = SubscriptionService(db, _StubMatcher())
    with pytest.raises(KeyError):
        await svc.get_bundle("nope")


@pytest.mark.asyncio
async def test_create_without_tenant_id_defaults_to_default(db):
    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("p1", ["auth config"])
    assert sub_id.startswith("sub_")

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == sub_id)
        )).scalar_one()
        assert row.tenant_id == DEFAULT_TENANT_ID


@pytest.mark.asyncio
async def test_all_ids_lists_every_subscription(db):
    svc = SubscriptionService(db, _StubMatcher())
    a = await svc.create("p1", ["x"])
    b = await svc.create("p2", ["y"])
    assert sorted(await svc.all_ids()) == sorted([a, b])


@pytest.mark.asyncio
async def test_expired_subscription_reads_as_missing(db):
    svc = SubscriptionService(db, _StubMatcher(), ttl_seconds=1)
    sub_id = await svc.create("p1", ["auth config"])
    await _age(db, sub_id)

    with pytest.raises(KeyError):
        await svc.get_bundle(sub_id)
    assert await svc.project_of(sub_id) is None


@pytest.mark.asyncio
async def test_reading_bundle_renews_lease(db):
    svc = SubscriptionService(db, _StubMatcher(), ttl_seconds=3600)
    sub_id = await svc.create("p1", ["auth config"])
    await _age(db, sub_id, seconds=60)

    await svc.get_bundle(sub_id)

    assert await _expires_in(db, sub_id) > timedelta(minutes=59)


async def _age(db, sub_id, seconds=-1):
    """Move a subscription's lease end to `seconds` from now."""
    async with db.session() as session:
        await session.execute(
            update(Subscription).where(Subscription.subscription_id == sub_id)
            .values(expires_at=datetime.now(timezone.utc) + timedelta(seconds=seconds))
        )
        await session.commit()


async def _expires_in(db, sub_id) -> timedelta:
    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == sub_id)
        )).scalar_one()
    return row.expires_at - datetime.now(timezone.utc)


class _CountingMatcher(_StubMatcher):
    def __init__(self):
        self.calls = 0

    async def match(self, *args, **kwargs):
        self.calls += 1
        return await super().match(*args, **kwargs)


@pytest.mark.asyncio
async def test_identical_creates_share_one_subscription_without_rematching(db):
    m = _CountingMatcher()
    svc = SubscriptionService(db, m)
    first = await svc.create("p1", ["b", "a"], top_k=5)
    again = await svc.create("p1", ["a", "b", "a"], top_k=5)

    assert again == first
    assert m.calls == 1
    assert await svc.all_ids() == [first]


@pytest.mark.asyncio
async def test_create_renews_shared_lease(db):
    svc = SubscriptionService(db, _StubMatcher(), ttl_seconds=3600)
    sub_id = await svc.create("p1", ["a"])
    await _age(db, sub_id, seconds=60)

    await svc.create("p1", ["a"])

    assert await _expires_in(db, sub_id) > timedelta(minutes=59)


@pytest.mark.asyncio
async def test_different_requests_get_different_subscriptions(db):
    svc = SubscriptionService(db, _StubMatcher())
    base = await svc.create("p1", ["a"])
    others = [
        await svc.create("p2", ["a"]),
        await svc.create("p1", ["b"]),
        await svc.create("p1", ["a"], top_k=3),
        await svc.create("p1", ["a"], threshold=0.5),
        await svc.create("p1", ["a"], scope={"since": "2026-01-01T00:00:00+00:00"}),
    ]
    assert base not in others
    assert len(set(others)) == len(others)


def test_tenants_never_share_a_subscription():
    assert _content_id("t1", "p1", ["a"], 5, None, None) != _content_id("t2", "p1", ["a"], 5, None, None)


@pytest.mark.asyncio
async def test_create_rematches_an_expired_subscription(db):
    m = _CountingMatcher()
    svc = SubscriptionService(db, m)
    sub_id = await svc.create("p1", ["a"])
    await _age(db, sub_id)

    assert await svc.create("p1", ["a"]) == sub_id
    assert m.calls == 2
    assert await _expires_in(db, sub_id) > timedelta(minutes=59)


@pytest.mark.asyncio
async def test_delete_notifies_everyone_sharing_the_subscription(db, notifier):
    svc = SubscriptionService(db, _StubMatcher())
    sub_id = await svc.create("p1", ["a"])
    queue = notifier.listen(sub_id)

    await svc.delete(sub_id)

    assert await asyncio.wait_for(queue.get(), 2.0) == sub_id
    with pytest.raises(KeyError):
        await svc.get_bundle(sub_id)
