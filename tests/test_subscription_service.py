import pytest
from sqlalchemy import select

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
