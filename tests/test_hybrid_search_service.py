import pytest
from src.core.hybrid_search_service import HybridSearchService


class _StubRanker:
    def __init__(self, ranking, scores=None):
        self._ranking = ranking
        # cosines the vector ranker can produce on demand; defaults to the
        # ranking's own scores, plus any extras outside the top_k window.
        self._scores = {**dict(ranking), **(scores or {})}

    async def search(self, project_id, query, top_k, since=None):
        return self._ranking[:top_k]

    async def score(self, project_id, query, node_keys, since=None):
        return {k: self._scores[k] for k in node_keys if k in self._scores}


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
async def test_reports_cosine_not_rrf_score():
    vector = _StubRanker([("a", 0.91), ("b", 0.82)])
    lexical = _StubRanker([("b", 5.0), ("a", 3.0)])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=3)
    scores = dict(results)
    # score is the vector cosine (0-1), not the tiny RRF weight (~0.016)
    assert scores["a"] == 0.91
    assert scores["b"] == 0.82


@pytest.mark.asyncio
async def test_scores_lexical_only_hits():
    # "d" is a keyword match the vector search ranked outside its top_k, but it
    # still has an embedding worth cosine 0.55 -> kept, not dropped.
    vector = _StubRanker([("a", 0.9)], scores={"d": 0.55})
    lexical = _StubRanker([("d", 4.0), ("a", 3.0)])
    service = HybridSearchService(vector, lexical, k=60)
    scores = dict(await service.search("p1", "q", top_k=10))
    assert scores["a"] == 0.9
    assert scores["d"] == 0.55


@pytest.mark.asyncio
async def test_drops_hits_with_no_embedding():
    # A lexical hit that has no embedding at all cannot be scored -> excluded.
    vector = _StubRanker([("a", 0.9)])
    lexical = _StubRanker([("ghost", 4.0), ("a", 3.0)])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=10)
    assert [doc_id for doc_id, _ in results] == ["a"]


@pytest.mark.asyncio
async def test_handles_one_empty_backend():
    vector = _StubRanker([("a", 0.9), ("b", 0.8)])
    lexical = _StubRanker([])
    service = HybridSearchService(vector, lexical, k=60)
    results = await service.search("p1", "q", top_k=10)
    assert [doc_id for doc_id, _ in results] == ["a", "b"]
