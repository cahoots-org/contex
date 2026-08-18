import pytest
from sentence_transformers import SentenceTransformer
from benchmark.eval import run_eval

_EXACT_TOKEN_SHAPES = ("config_keys", "error_ids", "codenames")


@pytest.mark.asyncio
async def test_pgfts_hybrid_beats_vector_on_exact_tokens(db):
    model = SentenceTransformer("all-MiniLM-L6-v2")
    await run_eval.load_dataset(db, model, project_id="eval")
    strategies = run_eval.build_strategies(db, model)

    hybrid = await run_eval.evaluate(strategies["pgfts_hybrid"], "eval", k=5)
    vector = await run_eval.evaluate(strategies["vector"], "eval", k=5)

    # --- (1) DISCRIMINATION GUARD -------------------------------------- #
    # With the adversarial distractors, pure vector search must be tempted
    # into mis-ranking on at least one exact-token shape, i.e. it must score
    # BELOW ceiling (recall < 1.0 OR mrr < 1.0) somewhere. This proves the
    # eval has teeth and guards against silently regressing to a trivially
    # perfect dataset where every strategy scores 1.00 (a tautology).
    def _below_ceiling(res, shape):
        s = res["by_shape"][shape]
        return s["recall"] < 1.0 or s["mrr"] < 1.0

    discriminating_shapes = [
        shape for shape in _EXACT_TOKEN_SHAPES if _below_ceiling(vector, shape)
    ]
    assert discriminating_shapes, (
        "Discrimination guard failed: vector-only scored at ceiling "
        "(recall==1.0 and mrr==1.0) on EVERY exact-token shape, so the eval "
        "proves nothing. Add harder distractors, or report the finding."
    )

    # --- (2) HYBRID NOT WORSE THAN VECTOR ------------------------------ #
    # On each shape where vector genuinely struggled, PG-FTS-hybrid must not
    # regress: it must be >= vector on both recall and mrr (the metrics that
    # capture whether the exact match is retrieved and ranked well).
    for shape in discriminating_shapes:
        v = vector["by_shape"][shape]
        h = hybrid["by_shape"][shape]
        assert h["recall"] >= v["recall"], (
            f"hybrid recall regressed vs vector on {shape}: "
            f"{h['recall']:.3f} < {v['recall']:.3f}"
        )
        assert h["mrr"] >= v["mrr"], (
            f"hybrid mrr regressed vs vector on {shape}: "
            f"{h['mrr']:.3f} < {v['mrr']:.3f}"
        )

    report = run_eval.render_report({"pgfts_hybrid": hybrid, "vector": vector})
    assert "pgfts_hybrid" in report and "config_keys" in report
