"""Lexical (keyword) search behind a backend-agnostic interface.

PgFtsLexical uses pg_search BM25 (paradedb.score / ||| match operator).
A future OpenSearchLexical can implement the same Protocol without touching
callers (design spec §3.3).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Collection, Optional, Protocol

from sqlalchemy import text

from src.core.recency import recency_sql_clause


class LexicalSearch(Protocol):
    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None, exclude_documents: Collection[str] = (),
    ) -> list[tuple[str, float]]:
        """Return (node_key, score) tuples, best match first."""
        ...


def _boost(name: str) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}")
    if not 0 < value <= 2048:
        raise ValueError(f"{name} must be above 0 and at most 2048, got {value}")
    return value


def _match(field: str, boost: float) -> str:
    # Safe to inline: boosts are validated floats. A typmod can't be a bind parameter,
    # and without the text cast Postgres types the parameter itself as a typmod-less boost.
    query = ":q" if boost == 1.0 else f"CAST(CAST(:q AS text) AS pdb.boost({boost}))"
    return f"{field} ||| {query}"


class PgFtsLexical:
    """pg_search BM25 lexical search over Embedding.description and data_original."""

    def __init__(self, db, description_boost: float = 1.0, data_boost: float = 1.0) -> None:
        self.db = db
        self.description_boost = description_boost
        self.data_boost = data_boost

    @classmethod
    def from_env(cls, db) -> "PgFtsLexical":
        """Field boosts from BM25_BOOST_DESCRIPTION and BM25_BOOST_DATA (default 1)."""
        return cls(db, _boost("BM25_BOOST_DESCRIPTION"), _boost("BM25_BOOST_DATA"))

    async def search(
        self, project_id: str, query: str, top_k: int,
        since: Optional[datetime] = None, exclude_documents: Collection[str] = (),
    ) -> list[tuple[str, float]]:
        sql = text(
            f"""
            SELECT node_key, paradedb.score(id) AS score
            FROM embeddings
            WHERE project_id = :project_id
              -- ||| tokenizes :q as plain text (OR of terms); @@@ would parse
              -- it as query syntax and fail on ', :, AND, unbalanced parens.
              AND ({_match("description", self.description_boost)}
                   OR {_match("data_original", self.data_boost)})
              {recency_sql_clause(since)}
              {"AND NOT (data_key = ANY(:excluded))" if exclude_documents else ""}
            ORDER BY score DESC
            LIMIT :top_k
            """
        )
        params = {"q": query, "project_id": project_id, "top_k": top_k}
        if since is not None:
            params["since"] = since
        if exclude_documents:
            params["excluded"] = list(exclude_documents)
        async with self.db.session() as session:
            result = await session.execute(sql, params)
            return [(row.node_key, float(row.score)) for row in result]
