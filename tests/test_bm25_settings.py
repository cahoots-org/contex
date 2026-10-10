"""BM25 tuning knobs: index-time tokenizer/k1/b and query-time field boosts (#144)."""
import pytest

from src.core.bm25_index import Bm25Settings, ensure_bm25_index
from src.core.db_models import Embedding
from src.core.lexical_search import PgFtsLexical


async def _add(db, node_key, description, data_original):
    async with db.session() as session:
        session.add(Embedding(
            project_id="p1", data_key=node_key, node_key=node_key, description=description,
            data={}, data_original=data_original, data_format="text", embedding=[0.0] * 768,
        ))


def test_settings_default_to_paradedb_defaults(monkeypatch):
    for name in ("BM25_TOKENIZER", "BM25_K1", "BM25_B"):
        monkeypatch.delenv(name, raising=False)
    assert Bm25Settings.from_env() == Bm25Settings("unicode_words", 1.2, 0.75)


@pytest.mark.parametrize("name,value", [
    ("BM25_TOKENIZER", "ngram; DROP TABLE embeddings"),
    ("BM25_K1", "101"),
    ("BM25_B", "1.5"),
    ("BM25_B", "abc"),
])
def test_invalid_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        Bm25Settings.from_env()


@pytest.mark.asyncio
async def test_index_built_with_defaults_is_left_alone(db):
    assert await ensure_bm25_index(db, Bm25Settings()) is False


@pytest.mark.asyncio
async def test_changed_settings_rebuild_the_index_once(db):
    await _add(db, "fn", "getUserName helper", "")
    try:
        assert await ensure_bm25_index(db, Bm25Settings("source_code", 0.9, 0.3)) is True
        assert await ensure_bm25_index(db, Bm25Settings("source_code", 0.9, 0.3)) is False
        # source_code splits camelCase, so the identifier matches its parts.
        assert [k for k, _ in await PgFtsLexical(db).search("p1", "user", top_k=5)] == ["fn"]
    finally:
        await ensure_bm25_index(db, Bm25Settings())


@pytest.mark.asyncio
async def test_field_boosts_reorder_results(db):
    await _add(db, "in_description", "invoice reconciliation", "unrelated")
    await _add(db, "in_data", "unrelated", "invoice reconciliation")

    by_description = PgFtsLexical(db, description_boost=5.0)
    by_data = PgFtsLexical(db, data_boost=5.0)
    assert (await by_description.search("p1", "invoice", top_k=2))[0][0] == "in_description"
    assert (await by_data.search("p1", "invoice", top_k=2))[0][0] == "in_data"


@pytest.mark.parametrize("name", ["BM25_BOOST_DESCRIPTION", "BM25_BOOST_DATA"])
def test_invalid_boost_is_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError):
        PgFtsLexical.from_env(db=None)
