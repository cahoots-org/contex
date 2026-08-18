import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_reconcile_honors_persisted_params(db, redis):
    # engine default max_matches=1 (restrictive); subscription created with top_k=10 (permissive)
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=1)
    await engine.initialize()
    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)
    # publish an array of objects — each element becomes a separate node, guaranteeing multiple
    # matching nodes exist for a semantic query against "database connection settings"
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db",
        data=[
            {"purpose": "database connection settings", "host": "localhost", "port": 5432},
            {"purpose": "database connection settings replica", "host": "replica.db", "port": 5432},
            {"purpose": "database connection pool settings", "max_conn": 10, "min_conn": 2},
        ],
        data_format="json",
    ))
    bundle = await engine.subscriptions.get_bundle(sub_id)
    total = sum(len(v) for v in bundle.values())
    # if reconcile used the engine default (max_matches=1) this would be 1; the persisted top_k=10 must win
    assert total > 1, f"reconcile ignored persisted top_k; got {total} matches"


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
