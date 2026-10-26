import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.core import embedder
from src.core.embedder import OnnxEmbedder, encode_async, encode_queries


def test_length_sorted_batches_keep_input_order():
    model = OnnxEmbedder("thenlper/gte-base")
    texts = ["a much longer sentence about invoices and payment processing", "login", "short text"]
    batched = model.encode(texts, batch_size=2)
    singles = np.vstack([model.encode(t) for t in texts])
    assert np.allclose(batched, singles, atol=1e-5)


def test_long_text_is_cut_before_tokenizing():
    model = OnnxEmbedder("thenlper/gte-base")
    tokenizer, seen = model.tokenizer, []
    model.tokenizer = SimpleNamespace(encode_batch=lambda texts: seen.extend(texts) or tokenizer.encode_batch(texts))
    model.encode("invoice " * 100_000)
    assert len(seen[0]) <= 512 * 16


class _ThreadRecorder:
    def __init__(self):
        self.thread = None

    def encode(self, texts, batch_size=32):
        self.thread = threading.current_thread()
        return np.zeros((len(texts), 4), dtype=np.float32)


@pytest.mark.asyncio
async def test_encode_async_runs_off_the_event_loop_thread():
    model = _ThreadRecorder()
    out = await encode_async(model, ["a", "b"], batch_size=8)
    assert out.shape == (2, 4)
    assert model.thread is not threading.main_thread()


class _Counting:
    def __init__(self):
        self.texts = []

    def encode(self, texts):
        self.texts += texts
        return np.stack([np.full(4, len(t), dtype=np.float32) for t in texts])


@pytest.mark.asyncio
async def test_encode_queries_reuses_cached_vectors():
    model = _Counting()
    first = await encode_queries(model, ["ab", "c", "ab"])
    again = await encode_queries(model, ["c", "ab"])
    assert model.texts == ["ab", "c"]
    assert [v[0] for v in first] == [2, 1, 2]
    assert np.array_equal(again[1], first[0])


@pytest.mark.asyncio
async def test_encode_queries_cache_is_per_model_and_bounded(monkeypatch):
    monkeypatch.setattr(embedder, "QUERY_CACHE_SIZE", 2)
    monkeypatch.setattr(embedder, "_query_vecs", embedder.OrderedDict())
    model, other = _Counting(), _Counting()
    await encode_queries(model, ["a", "b"])
    await encode_queries(other, ["a"])
    assert other.texts == ["a"]
    assert len(embedder._query_vecs) == 2
    await encode_queries(model, ["a"])
    assert model.texts == ["a", "b", "a"]


@pytest.mark.asyncio
async def test_encode_queries_caches_copies_not_views_of_the_batch():
    await encode_queries(_Counting(), ["a", "bb"])
    assert all(v.base is None for v in embedder._query_vecs.values())
