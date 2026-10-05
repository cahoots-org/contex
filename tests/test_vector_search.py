# tests/test_vector_search.py
import threading

import numpy as np
import pytest
from sqlalchemy import text
from src.core.db_models import Embedding
from src.core.embedder import OnnxEmbedder
from src.core.vector_search import PgVectorSearch


@pytest.mark.asyncio
async def test_semantically_closest_ranks_first(db):
    model = OnnxEmbedder("thenlper/gte-base")  # matches the default/column dim (768)
    async with db.session() as session:
        for key, descr in [("auth", "user authentication and login"),
                           ("billing", "invoice and payment processing")]:
            vec = model.encode(descr).tolist()
            session.add(Embedding(project_id="p1", data_key=key, node_key=key,
                                  description=descr, data={}, data_original=descr,
                                  data_format="text", embedding=vec))
        await session.commit()

    results = await PgVectorSearch(db, model).search("p1", "how do users sign in", top_k=10)
    assert results[0][0] == "auth"
    assert 0.0 <= results[0][1] <= 1.0


@pytest.mark.asyncio
async def test_hnsw_scan_widened_to_the_request(db):
    """The HNSW scan must not stop at ef_search=40 before the project filter."""
    search = PgVectorSearch(db, model=None)
    async with db.session() as session:
        await search._widen_hnsw_scan(session, top_k=100)
        assert await session.scalar(text("SHOW hnsw.ef_search")) == "100"
        assert await session.scalar(text("SHOW hnsw.iterative_scan")) == "strict_order"


@pytest.mark.asyncio
async def test_query_embedding_runs_off_the_event_loop(db):
    """Embedding is CPU-bound; running it on the loop stalls every request (#48)."""
    loop_thread = threading.current_thread()
    seen = []

    class _Model:
        def encode(self, text):
            seen.append(threading.current_thread())
            return np.zeros(768)

    search = PgVectorSearch(db, _Model())
    await search.search("p1", "q", top_k=1)
    await search.score("p1", "q", ["k"])
    assert seen and all(t is not loop_thread for t in seen)
