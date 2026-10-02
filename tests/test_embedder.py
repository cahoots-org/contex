import numpy as np

from src.core.embedder import OnnxEmbedder


def test_batch_matches_single_and_is_normalized():
    model = OnnxEmbedder("thenlper/gte-base")
    texts = ["short", "a much longer sentence so the batch has to pad the first text"]
    batch = model.encode(texts)
    assert batch.shape == (2, model.get_sentence_embedding_dimension()) == (2, 768)
    assert np.allclose(batch[0], model.encode(texts[0]), atol=1e-5)
    assert np.allclose(np.linalg.norm(batch, axis=1), 1.0, atol=1e-5)
