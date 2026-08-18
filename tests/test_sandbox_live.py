# tests/test_sandbox_live.py
import asyncio
import json

import pytest
from sqlalchemy import func, select

from src.core.context_engine import ContextEngine
from src.core.db_models import Subscription
from src.core.models import DataPublishEvent
from src.web.live import stream_subscription_updates


def _payload(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: "):].strip())


@pytest.mark.asyncio
async def test_stream_yields_initial_then_updates_then_cleans_up(db, redis):
    engine = ContextEngine(db=db, redis=redis, similarity_threshold=0.1, max_matches=10)
    await engine.initialize()

    agen = stream_subscription_updates(
        engine, "p", "database connection settings", top_k=10, threshold=0.1
    )

    # First frame is the initial bundle.
    first = _payload(await agen.__anext__())
    assert first["type"] == "bundle"
    assert first["updated_at"] is None

    # Publishing matching data reconciles -> emits subscription:{id}:updated -> next frame.
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db",
        data={"purpose": "database connection settings"}, data_format="json",
    ))

    second = _payload(await asyncio.wait_for(agen.__anext__(), timeout=5))
    assert second["type"] == "bundle"
    assert second["updated_at"] is not None
    # The freshly-published item appears in the streamed bundle.
    assert any(
        m["data_key"].startswith("db")
        for matches in second["bundle"].values()
        for m in matches
    )

    # Closing the stream deletes the ephemeral subscription.
    await agen.aclose()
    async with db.session() as session:
        count = (await session.execute(
            select(func.count()).select_from(Subscription).where(Subscription.project_id == "p")
        )).scalar_one()
    assert count == 0
