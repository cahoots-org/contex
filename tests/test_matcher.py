import pytest
from src.core.matcher import HybridMatcher


class _FakeSemanticMatcher:
    def __init__(self):
        self.calls = []

    # top_k/threshold are accepted so match() can call us, but not recorded here;
    # pass-through is asserted by test_matcher_threads_top_k_and_threshold_per_request.
    async def match_agent_needs(self, project_id, needs, top_k=None, threshold=None):
        self.calls.append((project_id, tuple(needs)))
        return {n: [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}] for n in needs}


@pytest.mark.asyncio
async def test_hybrid_matcher_delegates_and_returns_bundle_shape():
    sem = _FakeSemanticMatcher()
    matcher = HybridMatcher(sem)
    result = await matcher.match("p1", ["auth config"], metadata={"format": "json"})
    assert result == {"auth config": [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}]}
    assert sem.calls == [("p1", ("auth config",))]  # metadata is accepted but not passed through (seam only)


@pytest.mark.asyncio
async def test_matcher_threads_top_k_and_threshold_per_request():
    """top_k/threshold are passed through as arguments, not mutated on state."""

    class _RecordingSemanticMatcher:
        def __init__(self):
            self.calls = []

        async def match_agent_needs(self, project_id, needs, top_k=None, threshold=None):
            self.calls.append((project_id, tuple(needs), top_k, threshold))
            return {n: [] for n in needs}

    sem = _RecordingSemanticMatcher()
    matcher = HybridMatcher(sem)
    await matcher.match("p1", ["auth"], top_k=3, threshold=0.7)
    assert sem.calls == [("p1", ("auth",), 3, 0.7)]


@pytest.mark.asyncio
async def test_matcher_handles_multiple_needs():
    sem = _FakeSemanticMatcher()
    matcher = HybridMatcher(sem)
    result = await matcher.match("p1", ["a", "b"])
    # delegate called exactly once with both needs
    assert sem.calls == [("p1", ("a", "b"))]
    # result has an entry for each need
    assert set(result.keys()) == {"a", "b"}
    assert result["a"] == [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}]
    assert result["b"] == [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}]


@pytest.mark.asyncio
async def test_matcher_empty_needs_returns_empty():
    sem = _FakeSemanticMatcher()
    matcher = HybridMatcher(sem)
    result = await matcher.match("p1", [])
    # delegate is still called (HybridMatcher is a thin seam; it delegates unconditionally)
    assert sem.calls == [("p1", ())]
    # with no needs, the returned dict is empty
    assert result == {}


@pytest.mark.asyncio
async def test_matcher_passes_through_empty_matches():
    """Empty match lists are preserved in the returned bundle (not silently dropped)."""

    class _EmptyMatchSemanticMatcher:
        def __init__(self):
            self.calls = []

        async def match_agent_needs(self, project_id, needs, top_k=None, threshold=None):
            self.calls.append((project_id, tuple(needs)))
            # each need maps to an empty list — no matches found
            return {n: [] for n in needs}

    sem = _EmptyMatchSemanticMatcher()
    matcher = HybridMatcher(sem)
    result = await matcher.match("p1", ["x", "y"])
    assert result == {"x": [], "y": []}
    assert sem.calls == [("p1", ("x", "y"))]
