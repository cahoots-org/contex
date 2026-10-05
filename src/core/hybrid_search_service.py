"""Backend-agnostic hybrid search: fuse a vector ranker and a lexical ranker via RRF."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.core.rank_fusion import rrf_fuse


class HybridSearchService:
    def __init__(self, vector_search, lexical_search, k: int = 60) -> None:
        self.vector_search = vector_search
        self.lexical_search = lexical_search
        self.k = k

    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None, pool: Optional[int] = None,
    ) -> list[tuple[str, float]]:
        """Return up to ``pool`` (node_key, cosine_similarity), ordered by RRF fusion.

        The head is fused from each ranker's top ``top_k``: fusing deeper lists
        gives the weaker lexical ranker more say and costs recall. Hits beyond
        that depth follow as a tail, fused at full depth, for callers that
        over-fetch (e.g. to fill per-document collapsing).

        RRF fuses the vector and lexical rankings for ordering only; the score
        reported per result is the cosine similarity from the vector ranker, so
        it stays on the same 0-1 scale as vector-only search. RRF's own weights
        (~1/(k+rank)) are ordinal and not comparable to cosine.
        """
        pool = max(pool or top_k, top_k)
        vector_hits = await self.vector_search.search(project_id, query, pool, since=since)
        lexical_hits = await self.lexical_search.search(project_id, query, pool, since=since)
        cosine = dict(vector_hits)
        # Lexical-only hits fell outside the vector ranker's top_k window but are
        # still real embeddings; score them directly so they aren't dropped just
        # for ranking low semantically. This is where hybrid earns its keep.
        missing = [doc_id for doc_id, _ in lexical_hits if doc_id not in cosine]
        cosine.update(await self.vector_search.score(project_id, query, missing, since=since))
        rankings = [
            [doc_id for doc_id, _ in vector_hits],
            [doc_id for doc_id, _ in lexical_hits],
        ]
        head = [doc_id for doc_id, _ in rrf_fuse([r[:top_k] for r in rankings], k=self.k)]
        seen = set(head)
        tail = [doc_id for doc_id, _ in rrf_fuse(rankings, k=self.k) if doc_id not in seen]
        ranked = [(doc_id, cosine[doc_id]) for doc_id in head + tail if doc_id in cosine]
        return ranked[:pool]
