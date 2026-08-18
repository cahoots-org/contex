import pytest
from src.core.matcher import HybridMatcher


class _FakeSemanticMatcher:
    def __init__(self):
        self.calls = []

    async def match_agent_needs(self, project_id, needs):
        self.calls.append((project_id, tuple(needs)))
        return {n: [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}] for n in needs}


@pytest.mark.asyncio
async def test_hybrid_matcher_delegates_and_returns_bundle_shape():
    sem = _FakeSemanticMatcher()
    matcher = HybridMatcher(sem)
    result = await matcher.match("p1", ["auth config"], metadata={"format": "json"})
    assert result == {"auth config": [{"data_key": "k", "similarity": 0.8, "data": {}, "description": None}]}
    assert sem.calls == [("p1", ("auth config",))]  # metadata is accepted but not passed through (seam only)
