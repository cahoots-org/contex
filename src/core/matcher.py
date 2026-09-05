"""The Matcher seam: relevance matching behind a stable interface.

HybridMatcher delegates to SemanticDataMatcher.match_agent_needs, which routes
through HybridSearchService (pgvector + Postgres FTS + RRF) when hybrid search is
enabled, and pgvector cosine similarity otherwise. `metadata` (item format/type/
length) is accepted so a future LLM or tiered-model matcher can route on it
without changing callers.
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
        # Pass per-request params straight through; nothing is mutated on the matcher (#105).
        return await self.semantic_matcher.match_agent_needs(
            project_id, needs, top_k=top_k, threshold=threshold
        )
