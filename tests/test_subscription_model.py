import pytest
from sqlalchemy import select
from src.core.db_models import Subscription


@pytest.mark.asyncio
async def test_subscription_persists_needs_and_bundle(db):
    async with db.session() as session:
        session.add(Subscription(
            subscription_id="sub_1", project_id="p1",
            needs=["auth config"], scope=None,
            bundle={"auth config": [{"data_key": "cfg", "similarity": 0.9, "data": {}, "description": "d"}]},
        ))
        await session.commit()

    async with db.session() as session:
        row = (await session.execute(
            select(Subscription).where(Subscription.subscription_id == "sub_1")
        )).scalar_one()
        assert row.needs == ["auth config"]
        assert row.bundle["auth config"][0]["data_key"] == "cfg"
        assert row.project_id == "p1"
