"""Vector (semantic) ranker over pgvector, returning ranked node_keys for fusion."""
from __future__ import annotations

from sqlalchemy import select
from src.core.db_models import Embedding


class PgVectorSearch:
    def __init__(self, db, model) -> None:
        self.db = db
        self.model = model

    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        query_vec = self.model.encode(query).tolist()
        async with self.db.session() as session:
            result = await session.execute(
                select(
                    Embedding.node_key,
                    (1 - Embedding.embedding.cosine_distance(query_vec)).label("similarity"),
                )
                .where(Embedding.project_id == project_id)
                .order_by(Embedding.embedding.cosine_distance(query_vec))
                .limit(top_k)
            )
            return [(row.node_key, float(row.similarity)) for row in result]
