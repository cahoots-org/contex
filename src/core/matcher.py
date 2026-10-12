"""Relevance matching for subscriptions, delegating to SemanticDataMatcher."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


class HybridMatcher:
    def __init__(self, semantic_matcher) -> None:
        self.semantic_matcher = semantic_matcher

    async def match(
        self,
        project_id: str,
        needs: list[str],
        top_k: int | None = None,
        threshold: float | None = None,
        since: Optional[datetime] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return await self.semantic_matcher.match_agent_needs(
            project_id, needs, top_k=top_k, threshold=threshold, since=since
        )
