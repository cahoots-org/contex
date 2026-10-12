import threading
from types import SimpleNamespace

import numpy as np
import pytest

from src.core.embedder import OnnxEmbedder, encode_async


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
