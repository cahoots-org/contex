"""collapse_by_document: one result per document, best node first, capped related."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.semantic_matcher import collapse_by_document


def _hit(node, doc, sim):
    return {"data_key": node, "document": doc, "similarity": sim, "data": {}, "description": None}


def test_one_document_does_not_fill_top_k():
    hits = [_hit(f"jira:DEV-1.comments[{i}]", "jira:DEV-1", 0.9 - i * 0.01) for i in range(10)]
    hits.append(_hit("jira:DEV-2.root", "jira:DEV-2", 0.5))

    results = collapse_by_document(hits, top_k=5)

    assert [r["document"] for r in results] == ["jira:DEV-1", "jira:DEV-2"]
    assert results[0]["data_key"] == "jira:DEV-1.comments[0]"
    assert [r["data_key"] for r in results[0]["related"]] == [
        "jira:DEV-1.comments[1]", "jira:DEV-1.comments[2]",
    ]
    assert "document" not in results[0]["related"][0]


def test_top_k_counts_documents_and_later_hits_still_fill_related():
    hits = [_hit("a.1", "a", 0.9), _hit("b.1", "b", 0.8), _hit("c.1", "c", 0.7), _hit("a.2", "a", 0.6)]

    results = collapse_by_document(hits, top_k=2)

    assert [r["document"] for r in results] == ["a", "b"]
    assert [r["data_key"] for r in results[0]["related"]] == ["a.2"]
    assert results[1]["related"] == []
