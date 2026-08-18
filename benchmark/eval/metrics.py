"""Retrieval quality metrics over ranked id lists and a relevant-id set."""
from __future__ import annotations

import math


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k results that are relevant.

    Divides by k (not by how many results exist), so with a single relevant
    doc it is capped at 1/k — position within the top-k does not matter.
    """
    if k == 0:
        return 0.0
    top = ranked[:k]
    return sum(1 for doc_id in top if doc_id in relevant) / k


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Fraction of all relevant docs that appear anywhere in the top k.

    1.0 means every relevant doc was retrieved within k; position does not
    matter. Returns 0.0 when nothing is relevant (nothing to recall).
    """
    if not relevant:
        return 0.0
    top = set(ranked[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    """1 / (1-based rank of the first relevant result); 0.0 if none is found.

    Unlike recall, this rewards ranking a relevant doc *early*: rank 1 -> 1.0,
    rank 2 -> 0.5, rank 4 -> 0.25. Mean over queries is MRR.
    """
    for index, doc_id in enumerate(ranked, start=1):
        if doc_id in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at k (binary relevance).

    DCG sums a gain of 1 for each relevant hit, discounted by log2(rank+1) so
    later positions count less; it is then divided by the ideal DCG (all
    relevant docs packed at the top). Ranges 0.0 (no relevant docs in top k)
    to 1.0 (relevant docs ranked first). Like MRR it is position-sensitive,
    but it credits every relevant hit in the top k, not just the first.
    """
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, doc_id in enumerate(ranked[:k], start=1)
        if doc_id in relevant
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0
