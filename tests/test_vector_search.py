# tests/test_vector_search.py
import pytest
from sentence_transformers import SentenceTransformer
from src.core.db_models import Embedding
from src.core.vector_search import PgVectorSearch


@pytest.mark.asyncio
async def test_semantically_closest_ranks_first(db):
    model = SentenceTransformer("all-MiniLM-L6-v2")
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
