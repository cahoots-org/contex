"""resolve_format infers "code" from the data_key extension in the publish path.

Sources disagree on data_format (GitHub/S3 emit the generic "text"), so code
would route to the plain-text splitter and lose all structure. Centralized
detection fixes every connector + the publish tool in one place: a generic or
absent format falls back to the key's extension; an explicit non-generic format
always wins.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.core.node_converter import NodeConverter
from src.core.node_parsers import MAX_NODES, DocumentRejected, resolve_format, language_for_key


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
    assert resolve_format("text", "owner/repo:notes.txt") == "text"
    assert resolve_format(None, "notes.txt") is None


def test_document_extensions_name_their_format():
    assert resolve_format("text", "owner/repo:README.md") == "markdown"
    assert resolve_format("text", "bucket/deploy.YML") == "yaml"


def test_explicit_hint_picks_the_parser():
    result = NodeConverter().parse("Owner: platform-team\nStatus: draft", "markdown")
    assert result.format_name == "markdown"


def test_unhinted_list_is_json():
    assert NodeConverter().parse([{"a": 1}]).format_name == "json"


def test_key_value_prose_is_not_sniffed_as_yaml():
    notes = "Attendees: ana, bo\n\nDecision: ship it\n\nRisk: none"
    result = NodeConverter().parse(notes, "text")
    assert (result.format_name, len(result.nodes)) == ("text", 3)


def test_no_extension_no_signal():
    # The documented ceiling: no extension, no hint -> cannot detect.
    assert resolve_format(None, "snippet") is None
    assert resolve_format(None, None) is None


def test_extension_is_case_insensitive():
    assert resolve_format("text", "owner/repo:App.PY") == "code"


def test_unparseable_bytes_fail_instead_of_indexing_their_repr():
    result = NodeConverter().parse(b"%PDF-1.7 \x00\x01 truncated", "pdf")
    assert not result.success and result.nodes == []


def test_documents_over_the_node_limit_are_rejected_not_reparsed_as_text():
    rows = "a,b\n" + "\n".join(f"{i},x" for i in range(MAX_NODES + 1))
    with pytest.raises(DocumentRejected):
        NodeConverter().parse(rows, "csv")


def test_documents_with_colliding_paths_are_rejected():
    with pytest.raises(DocumentRejected):
        NodeConverter().parse({"": {"a": 1}, "b": 2}, "json")


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


def test_comment_inside_a_fenced_block_is_not_a_heading():
    result = NodeConverter().parse("# Install\n\n```bash\n# install deps\npip install x\n```", "markdown")
    assert [(n.node_type.value, n.content) for n in result.nodes] == [
        ("heading", "Install"), ("code_block", "# install deps\npip install x"),
    ]


def test_a_version_number_does_not_make_prose_markdown():
    result = NodeConverter().parse("We shipped version 2.5 today. It is faster. Try it now!")
    assert result.format_name == "text"
