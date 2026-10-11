"""Documents (PDF, DOCX, images) sent base64-encoded for Contex to extract.

Contex extracts the text server-side; connectors read it only to scan for
secrets. Images are only readable when the server has OCR_ENABLED.
"""
from __future__ import annotations

import base64
import io
import os
import re
import zipfile

import pypdfium2

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


def document_text(payload: str, data_format: str) -> str:
    """The text layer of a base64 document; "" for images, scans, and unreadable files."""
    body = base64.b64decode(payload)
    try:
        if data_format == "pdf":
            pdf = pypdfium2.PdfDocument(body)
            try:
                return "\n".join(page.get_textpage().get_text_bounded() for page in pdf).strip()
            finally:
                pdf.close()
        if data_format == "docx":
            with zipfile.ZipFile(io.BytesIO(body)) as docx:
                xml = " ".join(
                    docx.read(name).decode("utf-8", errors="replace")
                    for name in docx.namelist()
                    if name.startswith("word/") and name.endswith(".xml")
                )
            # Word splits text into runs, so join runs and break only at paragraphs.
            return re.sub(r"<[^>]+>", "", xml.replace("</w:p>", "\n")).strip()
    except Exception:  # noqa: BLE001 - a malformed file can raise anything; treat it as unreadable
        return ""
    return ""


def document_event(key: str, body: bytes, data_format: str, **kwargs) -> ChangeEvent:
    """An upsert carrying the document as base64."""
    return ChangeEvent(
        op="upsert", key=key, payload=base64.b64encode(body).decode("ascii"), data_format=data_format, **kwargs,
    )
