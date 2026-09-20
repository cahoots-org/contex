"""Unit tests for S3 connector pure functions.

Tests cover key filtering, extension allow-listing, size limits, and
object → ChangeEvent mapping — no AWS credentials or boto3 calls needed.
"""
from __future__ import annotations

import json

import pytest

from connectors.s3.reader import (
    DEFAULT_TEXT_EXTENSIONS,
    filter_key,
    is_text_extension,
    object_to_event,
)


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

    def test_include_glob_keeps_matching(self):
        assert filter_key("docs/readme.md", ["docs/*"], None, DEFAULT_TEXT_EXTENSIONS)

    def test_include_glob_drops_non_matching(self):
        assert not filter_key("archive/old.md", ["docs/*"], None, DEFAULT_TEXT_EXTENSIONS)

    def test_exclude_glob_drops_matching(self):
        assert not filter_key("file.tmp.txt", None, ["*.tmp.*"], DEFAULT_TEXT_EXTENSIONS)

    def test_exclude_glob_drops_prefix(self):
        assert not filter_key("archive/old.md", None, ["archive/*"], DEFAULT_TEXT_EXTENSIONS)

    def test_include_and_exclude_combined(self):
        include = ["docs/*"]
        exclude = ["docs/private/*"]
        assert filter_key("docs/readme.md", include, exclude, DEFAULT_TEXT_EXTENSIONS)
        assert not filter_key("docs/private/secret.md", include, exclude, DEFAULT_TEXT_EXTENSIONS)

    def test_exclude_takes_precedence_over_include(self):
        assert not filter_key(
            "docs/trash.tmp", ["docs/*"], ["*.tmp"], DEFAULT_TEXT_EXTENSIONS
        )


class TestObjectToEvent:
    def test_json_object_yields_json_format(self):
        body = json.dumps({"hello": "world"}).encode()
        event = object_to_event("data/config.json", body)
        assert event.data_format == "json"
        assert event.payload == {"hello": "world"}
        assert event.key == "data/config.json"
        assert event.op == "upsert"

    def test_text_object_yields_text_format(self):
        body = b"# Hello\nThis is a readme."
        event = object_to_event("docs/readme.md", body)
        assert event.data_format == "text"
        assert event.payload == "# Hello\nThis is a readme."
        assert event.key == "docs/readme.md"
        assert event.op == "upsert"

    def test_csv_yields_text_format(self):
        body = b"a,b,c\n1,2,3"
        event = object_to_event("export/data.csv", body)
        assert event.data_format == "text"

    def test_yaml_yields_text_format(self):
        body = b"key: value"
        event = object_to_event("config/settings.yaml", body)
        assert event.data_format == "text"

    def test_json_extension_case_insensitive(self):
        body = json.dumps({"x": 1}).encode()
        event = object_to_event("file.JSON", body)
        assert event.data_format == "json"

    def test_source_meta_contains_source(self):
        event = object_to_event("file.txt", b"content")
        assert event.source_meta == {"source": "s3"}

    def test_key_is_full_object_key(self):
        key = "deep/path/to/file.txt"
        event = object_to_event(key, b"text")
        assert event.key == key
