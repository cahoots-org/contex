import math
from benchmark.eval.metrics import (
    precision_at_k, recall_at_k, reciprocal_rank, ndcg_at_k,
)

RANKED = ["a", "x", "b", "y"]
RELEVANT = {"a", "b"}


def test_precision_at_k():
    assert precision_at_k(RANKED, RELEVANT, k=2) == 0.5   # a hit, x miss
    assert precision_at_k(RANKED, RELEVANT, k=4) == 0.5   # 2 of 4


def test_recall_at_k():
    assert recall_at_k(RANKED, RELEVANT, k=2) == 0.5      # found a of {a,b}
    assert recall_at_k(RANKED, RELEVANT, k=4) == 1.0      # found both


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, RELEVANT) == 1.0       # first item relevant
    assert reciprocal_rank(["x", "a"], {"a"}) == 0.5


def test_ndcg_at_k_perfect_is_one():
    assert math.isclose(ndcg_at_k(["a", "b"], {"a", "b"}, k=2), 1.0)


def test_ndcg_at_k_empty_relevant_is_zero():
    assert ndcg_at_k(RANKED, set(), k=4) == 0.0
