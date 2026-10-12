"""Secret-ingestion guard: detection, config switch, and runner skip."""
from __future__ import annotations

import asyncio
import io
import zipfile

from connectors.base import ChangeEvent, document_event, resolve_secret_scanner
from connectors.base.runner import run
from connectors.base.secrets import SecretScanner


# --- detection -------------------------------------------------------------

def test_flags_secret_filenames():
    s = SecretScanner()
    for key in ["acme/repo:.env", "acme/repo:deploy/id_rsa", "acme/repo:certs/server.pem"]:
        assert s.reason(key, "whatever") is not None
    assert s.reason("acme/repo:src/main.py", "x = 1") is None


def test_filename_match_ignores_case():
    s = SecretScanner()
    for key in ["r:.ENV", "r:server.PEM", "r:ID_RSA", "r:Credentials"]:
        assert s.reason(key, "x") is not None, key


def test_flags_prefixed_and_fine_grained_tokens():
    s = SecretScanner()
    for token in ["sk-proj-" + "A" * 40, "sk-ant-api03-" + "A" * 40, "github_pat_11ABCDEFG0" + "a" * 60]:
        assert s.reason("r:config.py", f"key={token}") is not None, token


def test_flags_secret_content_in_normal_file():
    s = SecretScanner()
    key = "acme/repo:config/settings.py"
    assert s.reason(key, 'AWS = "AKIAIOSFODNN7EXAMPLE"') is not None
    assert s.reason(key, "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n") is not None
    assert s.reason(key, "def add(a, b):\n    return a + b\n") is None


def _pdf(*pages: str | None) -> bytes:
    """A PDF with one Helvetica page per entry; "" is a blank page and None a page holding only an image."""
    n = len(pages)
    kids = b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content = b"BI /W 1 /H 1 /CS /G /BPC 8 ID \x00 EI" if text is None else f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R"
            b" /Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def _docx(xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_scans_document_text_not_base64():
    s = SecretScanner()
    secret_pdf = document_event("r:a.pdf", _pdf("key AKIAIOSFODNN7EXAMPLE"), "pdf")
    clean_pdf = document_event("r:b.pdf", _pdf("quarterly report", "", "appendix"), "pdf")
    split_docx = document_event("r:c.docx", _docx("<w:p><w:r><w:t>AKIAIOSF</w:t></w:r><w:r><w:t>ODNN7EXAMPLE</w:t></w:r></w:p>"), "docx")
    image = document_event("r:d.png", b"\x89PNG", "image")
    scanned_pdf = document_event("r:e.pdf", _pdf("quarterly report", None), "pdf")
    assert s.reason(secret_pdf.key, secret_pdf.payload, "pdf") is not None
    assert s.reason(clean_pdf.key, clean_pdf.payload, "pdf") is None
    assert s.reason(split_docx.key, split_docx.payload, "docx") is not None
    assert s.reason(image.key, image.payload, "image") is not None  # no text to scan
    assert s.reason(scanned_pdf.key, scanned_pdf.payload, "pdf") is not None


def test_reason_names_pattern_not_value():
    s = SecretScanner()
    why = s.reason("r:config.py", 'k = "AKIAIOSFODNN7EXAMPLE"')
    assert "AKIAIOSFODNN7EXAMPLE" not in why  # never leak the secret into logs


def test_scan_content_toggle_off_keeps_filename_check():
    s = SecretScanner(scan_content=False)
    assert s.reason("r:config.py", 'k = "AKIAIOSFODNN7EXAMPLE"') is None  # content not scanned
    assert s.reason("r:.env", "x") is not None                           # filename still blocked


# --- config switch ---------------------------------------------------------

def test_allow_secrets_disables_scanner():
    assert resolve_secret_scanner({"allow_secrets": True}) is None


def test_default_protects_and_configured_patterns_add_to_builtins():
    s = resolve_secret_scanner({"secrets": {"files": ["*.secret"]}})
    assert s is not None
    assert s.reason("r:vault.secret", "x") is not None   # configured pattern
    assert s.reason("r:.env", "x") is not None            # built-in still applies


# --- runner integration ----------------------------------------------------

class _RecordingPublisher:
    def __init__(self):
        self.published_keys = []

    async def publish_batch(self, items):
        self.published_keys.extend(i["data_key"] for i in items)
        return len(items)


def test_runner_skips_secret_events_and_counts_them():
    pub = _RecordingPublisher()
    events = [
        ChangeEvent(op="upsert", key="r:src/app.py", payload="x = 1", data_format="text"),
        ChangeEvent(op="upsert", key="r:.env", payload="TOKEN=abc", data_format="text"),
        ChangeEvent(op="upsert", key="r:config.py", payload='k="AKIAIOSFODNN7EXAMPLE"', data_format="text"),
    ]
    stats = asyncio.run(run(events, pub, secret_scanner=SecretScanner()))
    assert pub.published_keys == ["r:src/app.py"]
    assert stats.skipped_secrets == 2
    assert stats.published == 1
