import pytest
from src.core.hybrid_search_service import HybridSearchService


class _StubRanker:
    def __init__(self, ranking):
        self._ranking = ranking

    async def search(self, project_id, query, top_k):
        return self._ranking[:top_k]


@pytest.mark.asyncio
async def test_fuses_vector_and_lexical_rankings():
    vector = _StubRanker([("a", 0.9), ("b", 0.8), ("c", 0.7)])
    lexical = _StubRanker([("b", 5.0), ("d", 4.0), ("a", 3.0)])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=3)
    ids = [doc_id for doc_id, _ in results]
    # a and b appear in both rankings -> top two after fusion
    assert set(ids[:2]) == {"a", "b"}
    assert len(results) == 3


@pytest.mark.asyncio
async def test_handles_one_empty_backend():
    vector = _StubRanker([("a", 0.9), ("b", 0.8)])
    lexical = _StubRanker([])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=10)
    assert [doc_id for doc_id, _ in results] == ["a", "b"]
