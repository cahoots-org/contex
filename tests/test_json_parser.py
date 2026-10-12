"""JSONNodeParser keeps a record's own fields when it also has nested objects."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from src.core.node_parsers import JSONNodeParser, YAMLNodeParser

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
    assert nodes[""] == {
        "summary": "Follow meta-refresh redirects",
        "status": "Done",
        "labels": ["content"],
    }
    assert nodes["comments[0]"] == {"author": "alice", "body": "Shipped."}
    assert nodes["fields"] == {"priority": "High"}
    assert nodes["fields.reporter"] == {"name": "carol"}


def test_no_parent_node_when_only_nested_children():
    assert "" not in _by_path({"a": {"x": 1}, "b": [{"y": 2}]})



def test_dotted_keys_do_not_collide_with_nesting():
    paths = list(_by_path({"spring.datasource": {"url": "a"}, "spring": {"datasource": {"url": "b"}}}))
    assert len(paths) == len(set(paths)) == 2


def test_yaml_aliases_are_rejected():
    bomb = "a: &a {x: 1}\nb: &b [*a, *a, *a]\nc: [*b, *b, *b]"
    with pytest.raises(yaml.YAMLError):
        YAMLNodeParser().parse(bomb)


def test_top_level_key_named_root_does_not_replace_the_root_object():
    nodes = _by_path({"title": "Q3 plan", "root": {"a": 1}})
    assert nodes == {"": {"title": "Q3 plan"}, "root": {"a": 1}}
