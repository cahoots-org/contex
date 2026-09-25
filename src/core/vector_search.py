"""Vector (semantic) ranker over pgvector, returning ranked node_keys for fusion."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from src.core.db_models import Embedding
from src.core.recency import recency_filter


class PgVectorSearch:
    def __init__(self, db, model) -> None:
        self.db = db
        self.model = model

    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None,
    ) -> list[tuple[str, float]]:
        query_vec = self.model.encode(query).tolist()
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
            result = await session.execute(stmt)
            return [(row.node_key, float(row.similarity)) for row in result]

    async def score(
        self, project_id: str, query: str, node_keys: list[str],
        since: Optional[datetime] = None,
    ) -> dict[str, float]:
        """Cosine similarity for specific node_keys (keys without an embedding are omitted)."""
        if not node_keys:
            return {}
        query_vec = self.model.encode(query).tolist()
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
