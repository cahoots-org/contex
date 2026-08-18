"""Reciprocal Rank Fusion (RRF) for combining ranked result lists.

Cormack, Clarke & Buettcher (2009): fuse rankings by summing 1/(k+rank),
which is robust to differing score scales across retrievers.
"""
from __future__ import annotations


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
