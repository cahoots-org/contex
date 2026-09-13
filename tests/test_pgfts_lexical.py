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
async def test_exact_token_ranks_first(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "SERVICE_TIMEOUT_MS", top_k=10)
    assert results[0][0] == "timeout"
    assert all(isinstance(score, float) for _, score in results)


@pytest.mark.asyncio
async def test_non_matching_query_returns_empty(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "kubernetes ingress", top_k=10)
    assert results == []


@pytest.mark.asyncio
async def test_scoped_to_project(db):
    await _seed(db)
    results = await PgFtsLexical(db).search("other-project", "timeout", top_k=10)
    assert results == []


@pytest.mark.asyncio
async def test_partial_match_multi_term_query(db):
    # #138: a multi-term query where a doc contains a SUBSET of the terms must
    # still match. plainto_tsquery AND's every term, so this returned nothing and
    # hybrid search silently degraded to vector-only. Lexemes must be OR'd.
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "timeout kubernetes ingress", top_k=10)
    assert [node_key for node_key, _ in results] == ["timeout"]


@pytest.mark.asyncio
async def test_more_matching_terms_ranks_higher(db):
    # OR broadens candidates; ts_rank_cd must still order by relevance so a doc
    # matching more query terms outranks one matching fewer.
    await _seed(db)
    results = await PgFtsLexical(db).search("p1", "timeout retry", top_k=10)
    keys = [node_key for node_key, _ in results]
    assert set(keys) == {"timeout", "retry"}
