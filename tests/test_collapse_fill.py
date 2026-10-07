"""Collapsing to documents must still fill top_k when a few large documents
own the whole node pool."""
from unittest.mock import Mock, patch

import numpy as np
import pytest

from src.core.db_models import Embedding
from src.core.semantic_matcher import SemanticDataMatcher

DIM = 768


def _unit(*weights):
    v = np.zeros(DIM, dtype=np.float32)
    for i, w in weights:
        v[i] = w
    return (v / np.linalg.norm(v)).tolist()


@pytest.mark.asyncio
async def test_top_k_documents_filled_past_monopolizing_documents(db):
    model = Mock()
    model.get_sentence_embedding_dimension.return_value = DIM
    model.encode.side_effect = lambda x, *a, **k: np.array(_unit((0, 1.0)), dtype=np.float32)
    with patch("src.core.semantic_matcher._load_model", return_value=model):
        matcher = SemanticDataMatcher(db=db, similarity_threshold=0.0, max_matches=5)
    matcher.candidate_pool_factor = 10  # pool = 50 nodes

    rows = []
    for doc in ("big-a", "big-b"):  # 80 nodes, all nearer the query than any small doc
        rows += [(doc, f"{doc}-{i}", _unit((0, 1.0), (1, 0.1))) for i in range(40)]
    rows += [(f"small-{j}", f"small-{j}-0", _unit((0, 1.0), (2, 1.0))) for j in range(10)]
    async with db.session() as session:
        session.add_all([
            Embedding(project_id="p", data_key=doc, node_key=key, description=key,
                      data={}, data_original=key, data_format="text", embedding=vec)
            for doc, key, vec in rows
        ])
        await session.commit()

    result = await matcher.match_agent_needs("p", ["q"], top_k=5, threshold=0.0)
    documents = [m["document"] for m in result["q"]]
    assert len(documents) == 5
    assert set(documents[:2]) == {"big-a", "big-b"}  # nearest documents still lead


@pytest.mark.asyncio
async def test_fill_keeps_going_until_top_k_or_exhausted(db):
    # Every round's pool is owned by one large document, so filling 8 slots
    # takes 8 rounds; there is no round cap.
    model = Mock()
    model.get_sentence_embedding_dimension.return_value = DIM
    model.encode.side_effect = lambda x, *a, **k: np.array(_unit((0, 1.0)), dtype=np.float32)
    with patch("src.core.semantic_matcher._load_model", return_value=model):
        matcher = SemanticDataMatcher(db=db, similarity_threshold=0.0, max_matches=8)
    matcher.candidate_pool_factor = 1  # pool = 8 nodes

    rows = [
        (f"big-{d}", f"big-{d}-{i}", _unit((0, 1.0), (1, 0.1 * (d + 1))))
        for d in range(10) for i in range(10)
    ]
    async with db.session() as session:
        session.add_all([
            Embedding(project_id="p", data_key=doc, node_key=key, description=key,
                      data={}, data_original=key, data_format="text", embedding=vec)
            for doc, key, vec in rows
        ])
        await session.commit()

    result = await matcher.match_agent_needs("p", ["q"], top_k=8, threshold=0.0)
    assert [m["document"] for m in result["q"]] == [f"big-{d}" for d in range(8)]

    # Asking for more documents than exist returns all of them.
    result = await matcher.match_agent_needs("p", ["q"], top_k=50, threshold=0.0)
    assert len(result["q"]) == 10
