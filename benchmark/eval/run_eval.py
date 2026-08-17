"""Golden-eval runner: load dataset, run strategies, aggregate metrics, render report.

NOTE: OpenSearch-hybrid strategy is intentionally omitted here — it is added
in plan 3 for three-way comparison. Only 'vector' and 'pgfts_hybrid' are
included in this plan.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from statistics import mean

from sqlalchemy import text

from src.core.db_models import Embedding
from src.core.hybrid_search_service import HybridSearchService
from src.core.lexical_search import PgFtsLexical
from src.core.vector_search import PgVectorSearch
from benchmark.eval import metrics
from benchmark.eval.dataset import DOCUMENTS, QUERIES

_SHAPES = ["config_keys", "code_symbols", "error_ids", "codenames", "prose"]


async def load_dataset(db, model, project_id: str) -> None:
    """Insert DOCUMENTS as Embedding rows for the given project_id.

    Existing rows for this project are cleared first so the function is
    idempotent and results don't drift across repeated calls.
    """
    async with db.session() as session:
        # Clear any existing rows for this project to avoid duplicate-key errors
        # and metric drift across repeated runs.
        await session.execute(
            text("DELETE FROM embeddings WHERE project_id = :pid"),
            {"pid": project_id},
        )
        for doc in DOCUMENTS:
            text_for_vec = f"{doc['description']} {doc['data_original']}"
            session.add(Embedding(
                project_id=project_id,
                data_key=doc["node_key"],
                node_key=doc["node_key"],
                description=doc["description"],
                data={},
                data_original=doc["data_original"],
                data_format="text",
                embedding=model.encode(text_for_vec).tolist(),
            ))
        await session.commit()


def build_strategies(db, model) -> dict:
    """Return a dict of named search strategies, each exposing async search()."""
    vector = PgVectorSearch(db, model)
    lexical = PgFtsLexical(db)
    return {
        "vector": vector,
        "pgfts_hybrid": HybridSearchService(vector, lexical, k=60),
    }


async def evaluate(strategy_search, project_id: str, k: int = 5) -> dict:
    """Run all QUERIES against strategy_search and aggregate metrics by shape.

    Returns:
        {
            "overall": {precision, recall, mrr, ndcg},
            "by_shape": {shape: {precision, recall, mrr, ndcg}, ...}
        }
    """
    per_shape: dict[str, list[dict]] = {shape: [] for shape in _SHAPES}
    for q in QUERIES:
        hits = await strategy_search.search(project_id, q["query"], top_k=k)
        ranked = [doc_id for doc_id, _ in hits]
        rel = q["relevant"]
        per_shape[q["shape"]].append({
            "precision": metrics.precision_at_k(ranked, rel, k),
            "recall": metrics.recall_at_k(ranked, rel, k),
            "mrr": metrics.reciprocal_rank(ranked, rel),
            "ndcg": metrics.ndcg_at_k(ranked, rel, k),
        })

    def _agg(rows: list[dict]) -> dict:
        if not rows:
            return {m: 0.0 for m in ("precision", "recall", "mrr", "ndcg")}
        return {m: mean(r[m] for r in rows) for m in ("precision", "recall", "mrr", "ndcg")}

    all_rows = [r for rows in per_shape.values() for r in rows]
    # Keep every shape (empty ones aggregate to zeros) so callers can safely
    # index results["by_shape"][shape] without risking a KeyError.
    return {
        "overall": _agg(all_rows),
        "by_shape": {shape: _agg(rows) for shape, rows in per_shape.items()},
    }


def render_report(results_by_strategy: dict) -> str:
    """Render a markdown comparison table from evaluate() results."""
    lines = [
        "# Golden Eval Report",
        "",
        "> OpenSearch-hybrid strategy omitted in plan 1; added in plan 3 for comparison.",
        "",
        "| strategy | scope | precision | recall | mrr | ndcg |",
        "|---|---|---|---|---|---|",
    ]
    for name, res in results_by_strategy.items():
        o = res["overall"]
        lines.append(
            f"| {name} | overall | {o['precision']:.2f} | {o['recall']:.2f} | {o['mrr']:.2f} | {o['ndcg']:.2f} |"
        )
        for shape, s in res["by_shape"].items():
            lines.append(
                f"| {name} | {shape} | {s['precision']:.2f} | {s['recall']:.2f} | {s['mrr']:.2f} | {s['ndcg']:.2f} |"
            )
    return "\n".join(lines)


async def _main() -> None:
    """Convenience CLI entrypoint: loads data, runs eval, writes report.md."""
    import os
    from src.core.database import DatabaseManager
    from src.core.db_models import Base
    from sentence_transformers import SentenceTransformer

    db = DatabaseManager()
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://contex:contex_password@localhost:5432/contex_test",
    )
    await db.connect(database_url=database_url)

    # Mirror conftest fixture: ensure extension and tables exist.
    async with db.engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    model = SentenceTransformer("all-MiniLM-L6-v2")
    await load_dataset(db, model, project_id="eval")
    strategies = build_strategies(db, model)
    results = {name: await evaluate(s, "eval", k=5) for name, s in strategies.items()}
    report = render_report(results)

    report_path = Path(__file__).parent / "report.md"
    report_path.write_text(report)
    print(report)
    print(f"\nReport written to {report_path}")

    await db.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
