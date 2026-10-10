"""Documents are sent base64-encoded for the server to extract (#159)."""
from __future__ import annotations

import base64

import pytest

from connectors.base.documents import document_event, document_format, resolve_document_types


def test_pdf_and_docx_by_default():
    assert resolve_document_types({}) == frozenset({".pdf", ".docx"})


def test_images_opt_in_and_dots_are_optional():
    assert resolve_document_types({"documents": ["pdf", ".PNG"]}) == frozenset({".pdf", ".png"})


def test_documents_can_be_turned_off():
    assert resolve_document_types({"documents": []}) == frozenset()


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError, match=".xlsx"):
        resolve_document_types({"documents": [".xlsx"]})


def test_document_format_follows_enabled_types():
    types = frozenset({".pdf", ".jpg"})
    assert document_format("docs/Spec.PDF", types) == "pdf"
    assert document_format("scan.jpg", types) == "image"
    assert document_format("notes.docx", types) is None
    assert document_format("readme.md", types) is None


def test_document_event_carries_base64():
    ev = document_event("a.pdf", b"%PDF-1.7", "pdf", source_meta={"source": "s3"})
    assert (ev.op, ev.key, ev.data_format) == ("upsert", "a.pdf", "pdf")
    assert base64.b64decode(ev.payload) == b"%PDF-1.7"
    assert ev.source_meta == {"source": "s3"}
