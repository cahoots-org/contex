"""Unit tests for the NUL-byte stripper on the publish boundary.

Postgres rejects 0x00 in text/JSONB, so _strip_nul_bytes removes it from every
string in a published value before it reaches an INSERT. Pure function; no DB.
"""
from src.core.context_engine import _strip_nul_bytes


def test_strips_nul_from_plain_string():
    assert _strip_nul_bytes("a\x00b\x00c") == "abc"


def test_strips_nul_nested_in_dict_and_list():
    value = {"k": "x\x00y", "nested": [{"z": "1\x002"}, "keep\x00"]}
    assert _strip_nul_bytes(value) == {"k": "xy", "nested": [{"z": "12"}, "keep"]}


def test_leaves_clean_and_nonstring_values_untouched():
    assert _strip_nul_bytes("clean") == "clean"
    assert _strip_nul_bytes(42) == 42
    assert _strip_nul_bytes(None) is None
    assert _strip_nul_bytes(b"\x00bytes") == b"\x00bytes"  # bytes handled elsewhere


def test_demo():
    # Runnable self-check: a value that would otherwise fail the INSERT is cleaned.
    assert "\x00" not in _strip_nul_bytes({"content": "punkt\x00\x00pickle"})["content"]


if __name__ == "__main__":
    test_strips_nul_from_plain_string()
    test_strips_nul_nested_in_dict_and_list()
    test_leaves_clean_and_nonstring_values_untouched()
    test_demo()
    print("ok")
