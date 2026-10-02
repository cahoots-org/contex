"""resolve_format infers "code" from the data_key extension in the publish path.

Sources disagree on data_format (GitHub/S3 emit the generic "text"), so code
would route to the plain-text splitter and lose all structure. Centralized
detection fixes every connector + the publish tool in one place: a generic or
absent format falls back to the key's extension; an explicit non-generic format
always wins.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.node_parsers import resolve_format, language_for_key


def test_generic_text_with_code_extension_becomes_code():
    assert resolve_format("text", "owner/repo:src/app.py") == "code"
    assert resolve_format("text", "bucket/foo.ts") == "code"


def test_absent_format_with_code_extension_becomes_code():
    assert resolve_format(None, "owner/repo:lib/thing.go") == "code"


def test_explicit_nongeneric_format_always_wins():
    # A caller that says "json" means json, even for a .py key.
    assert resolve_format("json", "owner/repo:data.py") == "json"
    assert resolve_format("markdown", "notes.ts") == "markdown"


def test_non_code_extension_left_unchanged():
    assert resolve_format("text", "owner/repo:README.md") == "text"
    assert resolve_format(None, "notes.txt") is None


def test_no_extension_no_signal():
    # The documented ceiling: no extension, no hint -> cannot detect.
    assert resolve_format(None, "snippet") is None
    assert resolve_format(None, None) is None


def test_extension_is_case_insensitive():
    assert resolve_format("text", "owner/repo:App.PY") == "code"


def test_language_for_key_maps_extensions():
    assert language_for_key("owner/repo:a.py") == "python"
    assert language_for_key("b.tsx") == "tsx"
    assert language_for_key("c.unknown") is None
    assert language_for_key(None) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
