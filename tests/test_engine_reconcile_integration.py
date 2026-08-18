# tests/test_engine_reconcile_integration.py
import pytest
from src.core.context_engine import ContextEngine
from src.core.models import DataPublishEvent


@pytest.mark.asyncio
async def test_publish_reconciles_matching_subscription(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    # A subscription whose need should match the doc we publish.
    sub_id = await engine.subscriptions.create("proj", ["database connection settings"])

    await engine.publish_data(DataPublishEvent(
        project_id="proj", data_key="db_cfg",
        data={"host": "localhost", "port": 5432, "purpose": "database connection settings"},
        data_format="json",
    ))

    bundle = await engine.subscriptions.get_bundle(sub_id)
    # the published item now appears in the subscription's materialized bundle
    all_keys = [m["data_key"] for matches in bundle.values() for m in matches]
    assert any("db_cfg" in k for k in all_keys)
