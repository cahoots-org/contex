from src.core.rank_fusion import rrf_fuse


def test_doc_in_both_lists_outranks_doc_in_one():
    vector_ranking = ["a", "b", "c"]
    lexical_ranking = ["b", "d", "a"]
    fused = rrf_fuse([vector_ranking, lexical_ranking], k=60)
    ids = [doc_id for doc_id, _ in fused]
    # "a" (ranks 1 and 3) and "b" (ranks 2 and 1) appear in both -> top two
    assert set(ids[:2]) == {"a", "b"}
    # every input doc appears exactly once
    assert sorted(ids) == ["a", "b", "c", "d"]


def test_scores_use_reciprocal_rank_formula():
    fused = dict(rrf_fuse([["a", "b"]], k=60))
    assert fused["a"] == 1.0 / (60 + 1)
    assert fused["b"] == 1.0 / (60 + 2)


def test_empty_input_returns_empty():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []
