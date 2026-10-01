"""_embedding_text prefixes each chunk with its source key for provenance."""
from types import SimpleNamespace

from src.core.semantic_matcher import _embedding_text


def _node(text: str):
    return SimpleNamespace(get_text_content=lambda: text)


def test_prepends_data_key():
    out = _embedding_text("acme/repo:content/types/page_data.py", _node("class SourceType: ..."))
    assert out.startswith("acme/repo:content/types/page_data.py\n")
    assert "class SourceType" in out


def test_no_data_key_returns_bare_text():
    assert _embedding_text("", _node("hello")) == "hello"
