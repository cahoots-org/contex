import pytest
from src.core.subscriptions import SubscriptionService
from src.core.db_models import Embedding


class _StubMatcher:
    async def match(self, project_id, needs, metadata=None):
        return {n: [{"data_key": "cfg", "similarity": 0.9, "data": {"x": 1}, "description": "auth"}] for n in needs}


@pytest.mark.asyncio
async def test_create_materializes_bundle_and_get_bundle_reads_it(db, redis):
    svc = SubscriptionService(db, _StubMatcher(), redis)
    sub_id = await svc.create("p1", ["auth config"])
    assert sub_id.startswith("sub_")

    bundle = await svc.get_bundle(sub_id)
    assert bundle["auth config"][0]["data_key"] == "cfg"


@pytest.mark.asyncio
async def test_get_bundle_unknown_raises(db, redis):
    svc = SubscriptionService(db, _StubMatcher(), redis)
    with pytest.raises(KeyError):
        await svc.get_bundle("nope")
