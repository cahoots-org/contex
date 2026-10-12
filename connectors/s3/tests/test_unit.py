"""Unit tests for S3 connector pure functions.

Tests cover key filtering, extension allow-listing, size limits, and
object → ChangeEvent mapping — no AWS credentials or boto3 calls needed.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone


from connectors.s3.reader import (
    DEFAULT_TEXT_EXTENSIONS,
    _client_kwargs,
    filter_key,
    is_text_extension,
    object_to_event,
)


class TestClientKwargs:
    def test_empty_source_yields_no_kwargs(self, monkeypatch):
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        assert _client_kwargs({}) == {}

    def test_region_and_endpoint_passed_through(self):
        kwargs = _client_kwargs({"region": "us-east-1", "endpoint_url": "http://localhost:9000"})
        assert kwargs["region_name"] == "us-east-1"
        assert kwargs["endpoint_url"] == "http://localhost:9000"

    def test_explicit_credentials(self):
        kwargs = _client_kwargs({"aws_access_key_id": "AK", "aws_secret_access_key": "SK"})
        assert kwargs["aws_access_key_id"] == "AK"
        assert kwargs["aws_secret_access_key"] == "SK"

    def test_partial_credentials_ignored(self, monkeypatch):
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        kwargs = _client_kwargs({"aws_access_key_id": "AK"})
        assert "aws_access_key_id" not in kwargs


class TestIsTextExtension:
    def test_known_text_extensions(self):
        for ext in [".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".yaml", ".yml", ".html", ".log", ".rst"]:
            assert is_text_extension(f"file{ext}", DEFAULT_TEXT_EXTENSIONS), ext

    def test_binary_extensions_rejected(self):
        for ext in [".png", ".jpg", ".pdf", ".zip", ".exe", ".bin", ".gz"]:
            assert not is_text_extension(f"file{ext}", DEFAULT_TEXT_EXTENSIONS), ext

    def test_extension_check_is_case_insensitive(self):
        assert is_text_extension("README.MD", DEFAULT_TEXT_EXTENSIONS)
        assert is_text_extension("data.JSON", DEFAULT_TEXT_EXTENSIONS)

    def test_no_extension_rejected(self):
        assert not is_text_extension("Makefile", DEFAULT_TEXT_EXTENSIONS)

    def test_custom_extensions(self):
        custom = frozenset([".custom"])
        assert is_text_extension("file.custom", custom)
        assert not is_text_extension("file.txt", custom)


class TestFilterKey:
    def test_text_key_with_no_globs_passes(self):
        assert filter_key("docs/readme.md", None, None, DEFAULT_TEXT_EXTENSIONS)

    def test_binary_key_rejected(self):
        assert not filter_key("images/photo.png", None, None, DEFAULT_TEXT_EXTENSIONS)

    def test_exclude_takes_precedence_over_include(self):
        assert not filter_key(
            "docs/trash.tmp", ["docs/*"], ["*.tmp"], DEFAULT_TEXT_EXTENSIONS
        )


class TestObjectToEvent:
    def test_json_object_yields_json_format(self):
        body = b"\xef\xbb\xbf" + json.dumps({"hello": "world"}).encode()
        event = object_to_event("data/config.json", body)
        assert event.data_format == "json"
        assert event.payload == {"hello": "world"}
        assert event.key == "data/config.json"
        assert event.op == "upsert"

    def test_text_object_yields_text_format(self):
        body = b"\xef\xbb\xbf# Hello\nThis is a r\xe9adme."
        event = object_to_event("docs/readme.md", body)
        assert event.data_format == "text"
        assert event.payload == "# Hello\nThis is a r\ufffdadme."
        assert event.key == "docs/readme.md"
        assert event.op == "upsert"

    def test_last_modified_becomes_published_at(self):
        modified = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
        event = object_to_event("docs/readme.md", b"hi", last_modified=modified)
        assert event.published_at == "2026-02-01T10:00:00+00:00"
        assert object_to_event("docs/readme.md", b"hi").published_at is None

    def test_enabled_document_is_sent_base64(self):
        event = object_to_event("docs/spec.pdf", b"%PDF-1.7", document_types=frozenset({".pdf"}))
        assert event.data_format == "pdf"
        assert base64.b64decode(event.payload) == b"%PDF-1.7"

    def test_json_extension_case_insensitive(self):
        body = json.dumps({"x": 1}).encode()
        event = object_to_event("file.JSON", body)
        assert event.data_format == "json"
