"""Documents (PDF, DOCX, images) sent base64-encoded for Contex to extract.

Contex extracts the text server-side, so connectors need no parsing libraries.
Images are only readable when the server has OCR_ENABLED.
"""
from __future__ import annotations

import base64
import os

from .change_event import ChangeEvent

FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
}
DEFAULT_DOCUMENT_TYPES = (".pdf", ".docx")


def resolve_document_types(config: dict) -> frozenset[str]:
    """The extensions a connector sends as documents (``documents:`` in config)."""
    raw = config.get("documents")
    names = DEFAULT_DOCUMENT_TYPES if raw is None else raw
    types = frozenset(n.lower() if n.startswith(".") else f".{n.lower()}" for n in names)
    unknown = sorted(types - FORMATS.keys())
    if unknown:
        raise ValueError(f"unsupported document types {unknown}; supported: {sorted(FORMATS)}")
    return types


def document_format(key: str, document_types: frozenset[str]) -> str | None:
    """The data_format to send ``key`` as, or None when it isn't an enabled document."""
    ext = os.path.splitext(key.lower())[1]
    return FORMATS[ext] if ext in document_types else None


def document_event(key: str, body: bytes, data_format: str, **kwargs) -> ChangeEvent:
    """An upsert carrying the document as base64."""
    return ChangeEvent(
        op="upsert", key=key, payload=base64.b64encode(body).decode("ascii"), data_format=data_format, **kwargs,
    )
