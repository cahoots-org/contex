"""Backend-agnostic hybrid search: fuse a vector ranker and a lexical ranker via RRF."""
from __future__ import annotations

from src.core.rank_fusion import rrf_fuse


class HybridSearchService:
    def __init__(self, vector_search, lexical_search, k: int = 60) -> None:
        self.vector_search = vector_search
        self.lexical_search = lexical_search
        self.k = k

    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        vector_hits = await self.vector_search.search(project_id, query, top_k)
        lexical_hits = await self.lexical_search.search(project_id, query, top_k)
        rankings = [
            [doc_id for doc_id, _ in vector_hits],
            [doc_id for doc_id, _ in lexical_hits],
        ]
        return rrf_fuse(rankings, k=self.k)[:top_k]
