"""_embedding_text prefixes each chunk with its source key and document title."""
from types import SimpleNamespace

from src.core.node import Node, NodeType
from src.core.semantic_matcher import _document_title, _embedding_text


def _node(text: str):
    return SimpleNamespace(get_text_content=lambda: text)


def test_prepends_data_key():
    out = _embedding_text("acme/repo:content/types/page_data.py", _node("class SourceType: ..."))
    assert out.startswith("acme/repo:content/types/page_data.py\n")
    assert "class SourceType" in out


def test_no_data_key_returns_bare_text():
    assert _embedding_text("", _node("hello")) == "hello"


def test_prepends_document_title():
    out = _embedding_text("jira:DEV-1", _node("comments body: bumped pool size"), "Login times out")
    assert out.startswith("jira:DEV-1 Login times out\n")


def test_document_title_reads_root_title_field():
    nodes = [
        Node(path="root", content={"key": "DEV-1", "summary": "Login times out"}, node_type=NodeType.OBJECT),
        Node(path="comments[0]", content={"title": "not me"}, node_type=NodeType.OBJECT),
    ]
    assert _document_title(nodes) == "Login times out"


def test_document_title_empty_without_root():
    assert _document_title([Node(path="row_0", content={"name": "x"}, node_type=NodeType.ROW)]) == ""
