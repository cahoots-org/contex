"""Deleting documents by data_key."""
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from src.core.context_engine import ContextEngine
from src.core.db_models import Embedding, Symbol
from src.core.models import DataPublishEvent

CODE = "def connect_database(url):\n    return open_pool(url)\n\n\ndef open_pool(url):\n    return url\n"


@pytest_asyncio.fixture
async def engine(db):
    e = ContextEngine(db=db, similarity_threshold=0.1, max_matches=10)
    await e.initialize()
    return e


async def _count(db, model, data_key):
    async with db.session() as session:
        return await session.scalar(
            select(func.count()).select_from(model)
            .where(model.project_id == "p").where(model.data_key == data_key)
        )


@pytest.mark.asyncio
async def test_delete_removes_nodes_symbols_and_records_events(engine, db):
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="db.py", data=CODE, data_format="code",
    ))
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="cfg", data={"purpose": "database connection settings"},
    ))
    assert await _count(db, Symbol, "db.py") > 0

    result = await engine.delete_data("p", ["db.py", "nope"], source="mcp")

    assert result == {"deleted": ["db.py"], "missing": ["nope"]}
    assert await _count(db, Embedding, "db.py") == 0
    assert await _count(db, Symbol, "db.py") == 0
    assert await _count(db, Embedding, "cfg") > 0
    events = await engine.event_store.get_events_for_key("p", "db.py")
    assert events[0]["event_type"] == "db.py_deleted"
    assert await engine.event_store.get_events_for_key("p", "nope") == []


@pytest.mark.asyncio
async def test_delete_drops_the_document_from_subscription_bundles(engine):
    await engine.publish_data(DataPublishEvent(
        project_id="p", data_key="cfg", data={"purpose": "database connection settings"},
    ))
    sub_id = await engine.subscriptions.create("p", ["database connection settings"], top_k=10, threshold=0.1)
    before = await engine.subscriptions.get_bundle(sub_id)
    assert any(m["document"] == "cfg" for ms in before.values() for m in ms)

    await engine.delete_data("p", ["cfg"])

    after = await engine.subscriptions.get_bundle(sub_id)
    assert not any(m["document"] == "cfg" for ms in after.values() for m in ms)


@pytest.mark.asyncio
async def test_delete_is_scoped_to_the_project(engine, db):
    await engine.publish_data(DataPublishEvent(
        project_id="other", data_key="cfg", data={"purpose": "database connection settings"},
    ))
    result = await engine.delete_data("p", ["cfg"])
    assert result == {"deleted": [], "missing": ["cfg"]}
    async with db.session() as session:
        assert await session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.project_id == "other")
        ) > 0
