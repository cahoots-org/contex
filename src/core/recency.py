"""Shared recency predicate for time-windowed search.

Content is re-published in place (``updated_at`` bumped, ``created_at`` kept),
so freshness is ``COALESCE(updated_at, created_at)``. A single helper keeps the
vector, lexical, and vector-only search paths in agreement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func

from src.core.db_models import Embedding


def window_start(since: Optional[datetime], max_age_seconds: Optional[int]) -> Optional[datetime]:
    """The later of an absolute cutoff and a sliding window's start, or None when unbounded."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if max_age_seconds is None:
        return since
    start = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    return start if since is None else max(since, start)


def recency_filter(since: Optional[datetime]):
    """Return a SQLAlchemy predicate for ``since``, or None when unbounded."""
    if since is None:
        return None
    return func.coalesce(Embedding.updated_at, Embedding.created_at) >= since


def recency_sql_clause(since: Optional[datetime]) -> str:
    """Return a raw-SQL AND-clause for ``since``, or "" when unbounded.

    Used by the pg_search lexical path, which is expressed as raw SQL.
    """
    if since is None:
        return ""
    return "AND COALESCE(updated_at, created_at) >= :since"
