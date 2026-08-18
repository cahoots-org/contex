"""The Matcher seam: relevance matching behind a stable interface.

HybridMatcher delegates to SemanticDataMatcher.match_agent_needs, which already
routes through Plan 1's HybridSearchService (pgvector + Postgres FTS + RRF) when
hybrid is enabled. `metadata` (item format/type/length) is accepted so a future
LLM or tiered-model matcher can route on it without changing callers.
"""
from __future__ import annotations

from typing import Any, Protocol


class Matcher(Protocol):
    async def match(
        self,
        project_id: str,
        needs: list[str],
        metadata: dict | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        ...


class HybridMatcher:
    def __init__(self, semantic_matcher) -> None:
        self.semantic_matcher = semantic_matcher

    async def match(
        self,
        project_id: str,
        needs: list[str],
        metadata: dict | None = None,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        sm = self.semantic_matcher
        if top_k is None and threshold is None:
            return await sm.match_agent_needs(project_id, needs)
        old_max, old_thr = sm.max_matches, sm.threshold
        try:
            if top_k is not None:
                sm.max_matches = top_k
            if threshold is not None:
                sm.threshold = threshold
            return await sm.match_agent_needs(project_id, needs)
        finally:
            sm.max_matches, sm.threshold = old_max, old_thr
