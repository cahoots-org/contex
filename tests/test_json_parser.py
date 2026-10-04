"""JSONNodeParser keeps a record's own fields when it also has nested objects."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.node_parsers import JSONNodeParser

parser = JSONNodeParser()

ISSUE = {
    "summary": "Follow meta-refresh redirects",
    "status": "Done",
    "labels": ["content"],
    "comments": [
        {"author": "alice", "body": "Shipped."},
        {"author": "bob", "body": "Confirmed."},
    ],
    "fields": {"priority": "High", "reporter": {"name": "carol"}},
}


def _by_path(data):
    return {n.path: n.content for n in parser.parse(data).nodes}


def test_parent_primitives_kept_alongside_nested_children():
    nodes = _by_path(ISSUE)
    assert nodes["root"] == {
        "summary": "Follow meta-refresh redirects",
        "status": "Done",
        "labels": ["content"],
    }
    assert nodes["comments[0]"] == {"author": "alice", "body": "Shipped."}
    assert nodes["fields"] == {"priority": "High"}
    assert nodes["fields.reporter"] == {"name": "carol"}


def test_no_parent_node_when_only_nested_children():
    assert "root" not in _by_path({"a": {"x": 1}, "b": [{"y": 2}]})


def test_reconstruct_round_trips():
    assert parser.reconstruct(parser.parse(ISSUE).nodes) == ISSUE
