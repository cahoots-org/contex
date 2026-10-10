from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from src.core.db_models import Subscription
from src.core.subscriptions import SubscriptionService
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
