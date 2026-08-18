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


@pytest.mark.asyncio
async def test_publish_does_not_affect_other_project(db, redis):
    """Publishing to projA must not alter subscriptions that belong to projB."""
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    # Create a subscription in projB.
    sub_b = await engine.subscriptions.create("projB", ["database connection settings"])

    # Capture the bundle immediately after creation (should be empty — no data yet in projB).
    bundle_before = await engine.subscriptions.get_bundle(sub_b)

    # Publish a matching document to projA — a completely different project.
    await engine.publish_data(DataPublishEvent(
        project_id="projA", data_key="projA_db_cfg",
        data={"host": "projA-host", "port": 5432, "purpose": "database connection settings"},
        data_format="json",
    ))

    # projB's subscription bundle must be unchanged: projA's data must not have leaked in.
    bundle_after = await engine.subscriptions.get_bundle(sub_b)
    assert bundle_after == bundle_before

    # Sanity-check: no projA data key appears anywhere in projB's bundle.
    all_keys_b = [m["data_key"] for matches in bundle_after.values() for m in matches]
    assert not any("projA" in k for k in all_keys_b)
