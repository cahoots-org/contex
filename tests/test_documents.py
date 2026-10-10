"""PDF, DOCX and OCR'd documents published over MCP as base64 (#159)."""
import base64
import io
import json
import shutil

import pymupdf
import pytest
from docx import Document

from src.core import node_parsers
from src.core.context_engine import ContextEngine
from src.core.mcp_adapter import build_mcp_server

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")

PHRASE = "Quarterly revenue forecast for Atlantis"


def _pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), PHRASE, fontsize=14)
    return doc.tobytes()


def _png() -> bytes:
    page = pymupdf.open().new_page()
    page.insert_text((72, 100), PHRASE, fontsize=20)
    return page.get_pixmap(dpi=200).tobytes("png")


def _scanned_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_png())
    return doc.tobytes()


def _docx() -> bytes:
    doc = Document()
    doc.add_paragraph(PHRASE)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


async def _publish_and_query(db, data: bytes, data_format: str) -> list:
    engine = ContextEngine(db=db, similarity_threshold=0.0, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    await server.call_tool("contex_publish_batch", {"project_id": "docs", "items": [
        {"data_key": f"report.{data_format}", "data": base64.b64encode(data).decode(), "data_format": data_format},
    ]})
    result = await server.call_tool("contex_query", {
        "project_id": "docs", "query": "Atlantis revenue forecast", "top_k": 5, "threshold": 0.0,
    })
    return json.loads(result.content[0].text)["matches"]


@pytest.mark.asyncio
@pytest.mark.parametrize("make,data_format", [(_pdf, "pdf"), (_docx, "docx")])
async def test_base64_document_is_extracted(db, make, data_format):
    matches = await _publish_and_query(db, make(), data_format)
    assert matches and "Atlantis" in json.dumps(matches[0]["data"])


@pytest.mark.asyncio
async def test_non_base64_document_is_rejected(db):
    engine = ContextEngine(db=db, similarity_threshold=0.0, max_matches=10)
    await engine.initialize()
    server, _ = build_mcp_server(engine)
    with pytest.raises(Exception, match="base64"):
        await server.call_tool("contex_publish_batch", {"project_id": "docs", "items": [
            {"data_key": "r.pdf", "data": "not base64!", "data_format": "pdf"},
        ]})


@pytest.mark.asyncio
async def test_scanned_pages_are_skipped_without_ocr(db, monkeypatch):
    monkeypatch.setattr(node_parsers, "OCR_ENABLED", False)
    assert await _publish_and_query(db, _scanned_pdf(), "pdf") == []


@needs_tesseract
@pytest.mark.asyncio
async def test_scanned_pages_are_read_with_ocr(db, monkeypatch):
    monkeypatch.setattr(node_parsers, "OCR_ENABLED", True)
    matches = await _publish_and_query(db, _scanned_pdf(), "pdf")
    assert matches and "Atlantis" in json.dumps(matches[0]["data"])


@needs_tesseract
@pytest.mark.asyncio
async def test_images_are_read_with_ocr(db, monkeypatch):
    monkeypatch.setattr(node_parsers, "OCR_ENABLED", True)
    matches = await _publish_and_query(db, _png(), "image")
    assert matches and "Atlantis" in json.dumps(matches[0]["data"])
