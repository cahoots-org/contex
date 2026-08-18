# tests/test_subscription_consistency.py
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_query_matches_subscription_bundle(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    await engine.publish_data(DataPublishEvent(
        project_id="proj", data_key="db_cfg",
        data={"purpose": "database connection settings", "port": 5432}, data_format="json",
    ))

    need = "database connection settings"
    query_matches = await engine.query_project_data("proj", need, top_k=10, threshold=0.1)
    sub_id = await engine.subscriptions.create("proj", [need])
    bundle_matches = (await engine.subscriptions.get_bundle(sub_id))[need]

    assert query_matches, "expected non-empty query matches (test would be vacuous otherwise)"
    # what you test (query) is what you get (subscription): same data_keys, same order
    assert [m["data_key"] for m in query_matches] == [m["data_key"] for m in bundle_matches]


@pytest.mark.asyncio
async def test_delete_removes_subscription(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()
    sub_id = await engine.subscriptions.create("proj", ["anything"])
    await engine.subscriptions.delete(sub_id)
    with pytest.raises(KeyError):
        await engine.subscriptions.get_bundle(sub_id)
    await engine.subscriptions.delete(sub_id)  # idempotent — no error
