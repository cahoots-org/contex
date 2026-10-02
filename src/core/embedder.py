"""Sentence embeddings via ONNX Runtime.

Runs a model's published ``onnx/model.onnx`` export with mean pooling and L2
normalization, matching the sentence-transformers pipeline for mean-pooled
models (e.g. thenlper/gte-base) without pulling in torch.
"""

from typing import List, Union

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

ONNX_FILE = "onnx/model.onnx"


class OnnxEmbedder:
    def __init__(self, model_name: str, max_seq_length: int = 512):
        self.tokenizer = Tokenizer.from_file(hf_hub_download(model_name, "tokenizer.json"))
        self.tokenizer.enable_truncation(max_seq_length)
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
        return np.vstack(
            [self._encode_batch(texts[i : i + batch_size]) for i in range(0, len(texts), batch_size)]
        )

    def _encode_batch(self, texts: List[str]) -> np.ndarray:
        encodings = self.tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self.session.run(None, feed)[0]
        pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        return (pooled / np.linalg.norm(pooled, axis=1, keepdims=True)).astype(np.float32)
