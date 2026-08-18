import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_create_with_params_matches_query_with_same_params(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.9, max_matches=1)
    await engine.initialize()
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db", data={"purpose": "database connection settings"}, data_format="json",
    ))
    need = "database connection settings"
    # query with explicit permissive params
    q = await engine.query_project_data("p", need, top_k=10, threshold=0.1)
    # a subscription created with the SAME params must yield the same matches,
    # even though the engine's defaults (threshold 0.9 / max 1) would differ
    sub_id = await engine.subscriptions.create("p", [need], top_k=10, threshold=0.1)
    bundle = await engine.subscriptions.get_bundle(sub_id)
    assert [m["data_key"] for m in q] == [m["data_key"] for m in bundle[need]]
    assert len(bundle[need]) >= 1
