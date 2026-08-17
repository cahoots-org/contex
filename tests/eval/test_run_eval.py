import pytest
from sentence_transformers import SentenceTransformer
from benchmark.eval import run_eval


@pytest.mark.asyncio
async def test_pgfts_hybrid_beats_vector_on_exact_tokens(db):
    model = SentenceTransformer("all-MiniLM-L6-v2")
    await run_eval.load_dataset(db, model, project_id="eval")
    strategies = run_eval.build_strategies(db, model)

    hybrid = await run_eval.evaluate(strategies["pgfts_hybrid"], "eval", k=5)
    vector = await run_eval.evaluate(strategies["vector"], "eval", k=5)

    # Exact-token shapes are where lexical must help; hybrid should be >= vector.
    for shape in ("config_keys", "error_ids", "codenames"):
        assert hybrid["by_shape"][shape]["recall"] >= vector["by_shape"][shape]["recall"]

    report = run_eval.render_report({"pgfts_hybrid": hybrid, "vector": vector})
    assert "pgfts_hybrid" in report and "config_keys" in report
