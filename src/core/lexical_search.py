"""Lexical (keyword/BM25-style) search behind a backend-agnostic interface.

PgFtsLexical uses Postgres full-text search (ts_rank_cd + plainto_tsquery).
A future OpenSearchLexical can implement the same Protocol without touching
callers (design spec §3.3).
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import text


class LexicalSearch(Protocol):
    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        """Return (node_key, score) tuples, best match first."""
        ...


class PgFtsLexical:
    """Postgres full-text lexical search over Embedding.search_text."""

    def __init__(self, db) -> None:
        self.db = db

    async def search(
        self, project_id: str, query: str, top_k: int
    ) -> list[tuple[str, float]]:
        sql = text(
            """
            SELECT node_key,
                   ts_rank_cd(search_text, plainto_tsquery('english', :q)) AS score
            FROM embeddings
            WHERE project_id = :project_id
              AND search_text @@ plainto_tsquery('english', :q)
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        async with self.db.session() as session:
            result = await session.execute(
                sql, {"q": query, "project_id": project_id, "top_k": top_k}
            )
            return [(row.node_key, float(row.score)) for row in result]
