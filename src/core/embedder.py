"""Sentence embeddings via ONNX Runtime.

Runs a model's published ``onnx/model.onnx`` export with mean pooling and L2
normalization, matching the sentence-transformers pipeline for mean-pooled
models (e.g. thenlper/gte-base) without pulling in torch.
"""

import asyncio
from collections import OrderedDict
from typing import List, Union

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from src.core.limits import MAX_EMBED_CHARS, MAX_SEQ_LENGTH

ONNX_FILE = "onnx/model.onnx"

# Query vectors kept per process, ~3KB each at 768 dims.
QUERY_CACHE_SIZE = 4096
_query_vecs: OrderedDict[tuple, np.ndarray] = OrderedDict()


async def encode_async(model, texts, **kwargs):
    """Run ``model.encode`` in a worker thread so inference never blocks the event loop."""
    return await asyncio.to_thread(model.encode, texts, **kwargs)


async def encode_queries(model, texts: List[str]) -> List[np.ndarray]:
    """Embed query texts, reusing vectors cached per (model, text) in a bounded LRU."""
    found = {t: _query_vecs[(model, t)] for t in set(texts) if (model, t) in _query_vecs}
    missing = sorted(set(texts) - found.keys())
    if missing:
        # Copies, so a cached row doesn't keep its whole batch alive.
        found.update((t, v.copy()) for t, v in zip(missing, await encode_async(model, missing)))
    for t, vec in found.items():
        _query_vecs[(model, t)] = vec
        _query_vecs.move_to_end((model, t))
    while len(_query_vecs) > QUERY_CACHE_SIZE:
        _query_vecs.popitem(last=False)
    return [found[t] for t in texts]


class OnnxEmbedder:
    def __init__(self, model_name: str):
        self.tokenizer = Tokenizer.from_file(hf_hub_download(model_name, "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_SEQ_LENGTH)
        self.tokenizer.enable_padding()
        self.session = ort.InferenceSession(
            hf_hub_download(model_name, ONNX_FILE), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}
        self._dim = self.session.get_outputs()[0].shape[-1]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts: Union[str, List[str]], batch_size: int = 32) -> np.ndarray:
        """Embed one text (returns shape ``(dim,)``) or a list (``(n, dim)``)."""
        if isinstance(texts, str):
            return self.encode([texts], batch_size)[0]
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        # Each batch pads to its longest text, so batch similar lengths together
        # and scatter the results back into input order.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        out = np.empty((len(texts), self._dim), dtype=np.float32)
        for start in range(0, len(order), batch_size):
            idx = order[start : start + batch_size]
            out[idx] = self._encode_batch([texts[i] for i in idx])
        return out

    def _encode_batch(self, texts: List[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch([t[:MAX_EMBED_CHARS] for t in texts])
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self.session.run(None, feed)[0]
        pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return (pooled / np.linalg.norm(pooled, axis=1, keepdims=True)).astype(np.float32)
