"""Vector (semantic) ranker over pgvector, returning ranked node_keys for fusion."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import select, text
from src.core.db_models import Embedding
from src.core.recency import recency_filter


# pgvector's cap on hnsw.ef_search.
_MAX_EF_SEARCH = 1000


class PgVectorSearch:
    def __init__(self, db, model) -> None:
        self.db = db
        self.model = model
        self._iterative_scan: Optional[bool] = None

    async def _widen_hnsw_scan(self, session, top_k: int) -> None:
        """Let the HNSW scan return ``top_k`` rows after the project filter.

        By default the scan stops at ``hnsw.ef_search`` (40) candidates and the
        project filter runs afterwards, so other projects' rows use up slots.
        Iterative scans (pgvector 0.8+) keep scanning until the limit is met.
        """
        if self._iterative_scan is None:
            version = await session.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            self._iterative_scan = bool(version) and tuple(
                int(part) for part in version.split(".")[:2]
            ) >= (0, 8)
        ef_search = min(max(top_k, 40), _MAX_EF_SEARCH)
        await session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
        if self._iterative_scan:
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))

    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None,
    ) -> list[tuple[str, float]]:
        query_vec = (await asyncio.to_thread(self.model.encode, query)).tolist()
        stmt = (
            select(
                Embedding.node_key,
                (1 - Embedding.embedding.cosine_distance(query_vec)).label("similarity"),
            )
            .where(Embedding.project_id == project_id)
            .order_by(Embedding.embedding.cosine_distance(query_vec))
            .limit(top_k)
        )
        recency = recency_filter(since)
        if recency is not None:
            stmt = stmt.where(recency)
        async with self.db.session() as session:
            await self._widen_hnsw_scan(session, top_k)
            result = await session.execute(stmt)
            return [(row.node_key, float(row.similarity)) for row in result]

    async def score(
        self, project_id: str, query: str, node_keys: list[str],
        since: Optional[datetime] = None,
    ) -> dict[str, float]:
        """Cosine similarity for specific node_keys (keys without an embedding are omitted)."""
        if not node_keys:
            return {}
        query_vec = (await asyncio.to_thread(self.model.encode, query)).tolist()
        stmt = (
            select(
                Embedding.node_key,
                (1 - Embedding.embedding.cosine_distance(query_vec)).label("similarity"),
            )
            .where(Embedding.project_id == project_id)
            .where(Embedding.node_key.in_(node_keys))
        )
        recency = recency_filter(since)
        if recency is not None:
            stmt = stmt.where(recency)
        async with self.db.session() as session:
            result = await session.execute(stmt)
            return {row.node_key: float(row.similarity) for row in result}
