import pytest
from src.core.db_models import Embedding
from src.core.lexical_search import PgFtsLexical


async def _seed(db):
    async with db.session() as session:
        session.add_all([
            Embedding(project_id="p1", data_key="cfg", node_key="timeout",
                      description="request timeout setting",
                      data={}, data_original="SERVICE_TIMEOUT_MS=30000",
                      data_format="text", embedding=[0.0] * 384),
            Embedding(project_id="p1", data_key="cfg", node_key="retry",
                      description="retry policy",
                      data={}, data_original="SERVICE_RETRY_MS=1000",
                      data_format="text", embedding=[0.0] * 384),
        ])
        await session.commit()


@pytest.mark.asyncio
async def test_exact_token_matches(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "SERVICE_TIMEOUT_MS", top_k=10)
    assert results[0][0] == "timeout"
    assert all(isinstance(s, float) for _, s in results)


@pytest.mark.asyncio
async def test_partial_multi_term_match(db):
    # BM25 OR-matches: a doc containing a subset of terms still matches.
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "timeout kubernetes ingress", top_k=10)
    assert [k for k, _ in results] == ["timeout"]


@pytest.mark.asyncio
async def test_matches_data_original(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "SERVICE_RETRY_MS", top_k=10)
    assert results[0][0] == "retry"


@pytest.mark.asyncio
async def test_scoped_to_project(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("other-project", "timeout", top_k=10)
    assert results == []
