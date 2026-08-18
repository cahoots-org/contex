# tests/test_embedding_search_text.py
import pytest
from sqlalchemy import select, text
from src.core.db_models import Embedding


@pytest.mark.asyncio
async def test_search_text_is_populated_and_matches(db):
    async with db.session() as session:
        session.add(Embedding(
            project_id="p1", data_key="cfg", node_key="cfg::timeout",
            description="Service request timeout configuration",
            data={"timeout_ms": 30000}, data_original="SERVICE_TIMEOUT_MS=30000",
            data_format="text", embedding=[0.0] * 384,
        ))
        await session.commit()

    async with db.session() as session:
        row = await session.execute(
            select(Embedding.node_key)
            .where(Embedding.project_id == "p1")
            .where(text("search_text @@ plainto_tsquery('english', 'timeout configuration')"))
        )
        assert row.scalar_one() == "cfg::timeout"
