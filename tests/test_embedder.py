import numpy as np

from src.core.embedder import OnnxEmbedder


def test_length_sorted_batches_keep_input_order():
    model = OnnxEmbedder("thenlper/gte-base")
    texts = ["a much longer sentence about invoices and payment processing", "login", "short text"]
    batched = model.encode(texts, batch_size=2)
    singles = np.vstack([model.encode(t) for t in texts])
    assert np.allclose(batched, singles, atol=1e-5)
