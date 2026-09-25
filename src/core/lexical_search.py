"""Lexical (keyword) search behind a backend-agnostic interface.

PgFtsLexical uses pg_search BM25 (paradedb.score / @@@ operator).
A future OpenSearchLexical can implement the same Protocol without touching
callers (design spec §3.3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from sqlalchemy import text

from src.core.recency import recency_sql_clause


class LexicalSearch(Protocol):
    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None,
    ) -> list[tuple[str, float]]:
        """Return (node_key, score) tuples, best match first."""
        ...


class PgFtsLexical:
    """pg_search BM25 lexical search over Embedding.description and data_original."""

    def __init__(self, db) -> None:
        self.db = db

    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None,
    ) -> list[tuple[str, float]]:
        sql = text(
            f"""
            SELECT node_key, paradedb.score(id) AS score
            FROM embeddings
            WHERE project_id = :project_id
              AND (description @@@ :q OR data_original @@@ :q)
              {recency_sql_clause(since)}
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        params = {"q": query, "project_id": project_id, "top_k": top_k}
        if since is not None:
            params["since"] = since
        async with self.db.session() as session:
            result = await session.execute(sql, params)
            return [(row.node_key, float(row.score)) for row in result]
