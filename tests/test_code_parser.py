"""CodeNodeParser turns source into structural nodes + defs/refs via tree-sitter.

Code was the only content type without a real parser: it got shredded into
blank-line blobs, losing every function/class boundary and all cross-file
structure (imports, call targets). This parser emits function/class/method nodes
with proper nesting (node_path) and records, per node, the names it defines
(defs) and the names it references (refs: imports + call targets) in node
metadata — the raw material for the symbols table and cross-file linking.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.node import NodeType
from src.core.node_converter import NodeConverter
from src.core.node_parsers import CodeNodeParser

nc = NodeConverter()

PY = '''\
import os
from mypkg.helpers import load_config, Thing

def top_level(a, b):
    load_config(a)
    return os.path.join(a, b)

class Service:
    def __init__(self):
        self.x = Thing()

    def run(self):
        return top_level(self.x, 2)
'''

TS = '''\
import { load, Thing } from "./helpers";

export function topLevel(a: number): number {
  load(a);
  return a + 1;
}

export class Service {
  run(): number { return topLevel(this.x); }
}

const arrow = (z) => helper(z);
'''

GO = '''\
package main
import h "ex.com/helpers"

func Plain(a int) int { return a }

type Service struct { x int }

func (s *Service) Run() int { return Plain(h.Load(s.x)) }
'''


def _by_path(nodes):
    return {n.path: n for n in nodes}


def test_can_parse_only_on_code_hint():
    p = CodeNodeParser()
    assert p.can_parse("whatever", "code")
    assert not p.can_parse("whatever", "text")
    assert not p.can_parse("whatever", None)


def test_python_structural_nodes_and_nesting():
    res = nc.parse(PY, "code", data_key="repo:app.py")
    assert res.success and res.format_name == "code"
    by = _by_path(res.nodes)
    assert by["top_level"].node_type == NodeType.FUNCTION
    assert by["Service"].node_type == NodeType.CLASS
    # Methods nest under the class -> dotted node_path, typed METHOD.
    assert by["Service.run"].node_type == NodeType.METHOD
    assert by["Service.__init__"].node_type == NodeType.METHOD


def test_python_defs_and_refs():
    res = nc.parse(PY, "code", data_key="repo:app.py")
    by = _by_path(res.nodes)
    assert by["top_level"].metadata["defs"] == ["top_level"]
    # Calls inside the function body are refs on that node.
    assert "load_config" in by["top_level"].metadata["refs"]
    assert "join" in by["top_level"].metadata["refs"]
    assert "top_level" in by["Service.run"].metadata["refs"]
    # Imported symbols are module-level refs on the file node (path "").
    mod = by[""]
    assert "load_config" in mod.metadata["refs"]
    assert "Thing" in mod.metadata["refs"]
    assert mod.metadata["defs"] == []


def test_typescript_including_arrow_const():
    res = nc.parse(TS, "code", data_key="repo:app.ts")
    by = _by_path(res.nodes)
    assert by["topLevel"].node_type == NodeType.FUNCTION
    assert by["Service"].node_type == NodeType.CLASS
    assert by["Service.run"].node_type == NodeType.METHOD
    # const arrow = () => ... is a named function def.
    assert by["arrow"].node_type == NodeType.FUNCTION
    assert "helper" in by["arrow"].metadata["refs"]
    assert "load" in by[""].metadata["refs"]  # named import


def test_go_funcs_types_methods():
    res = nc.parse(GO, "code", data_key="repo:app.go")
    by = _by_path(res.nodes)
    assert by["Plain"].node_type == NodeType.FUNCTION
    assert by["Service"].node_type == NodeType.CLASS  # type -> class node
    assert by["Run"].node_type == NodeType.METHOD
    assert "Plain" in by["Run"].metadata["refs"]
    assert "Load" in by["Run"].metadata["refs"]  # selector call h.Load


def test_bytes_input_is_decoded():
    res = nc.parse(PY.encode("utf-8"), "code", data_key="repo:app.py")
    assert res.success and any(n.path == "top_level" for n in res.nodes)


def test_unsupported_extension_falls_back_to_text():
    # No grammar for the key -> parser fails -> converter falls back, no crash.
    res = nc.parse(PY, "code", data_key="repo:app.unknownext")
    assert res.format_name != "code"


def test_no_data_key_falls_back():
    res = nc.parse(PY, "code")
    assert res.format_name != "code"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")


def test_duplicate_names_get_unique_paths():
    # Property getter/setter (and overloads) share a name; node_path must stay
    # unique or the (project_id, node_key) unique index rejects the insert.
    src = '''\
class ArticleModel:
    @property
    def version(self):
        return self._v

    @version.setter
    def version(self, v):
        self._v = v
'''
    res = nc.parse(src, "code", data_key="repo:m.py")
    paths = [n.path for n in res.nodes]
    assert len(paths) == len(set(paths))
    by = _by_path(res.nodes)
    assert by["ArticleModel.version"].metadata["defs"] == ["version"]
    assert by["ArticleModel.version#2"].metadata["defs"] == ["version"]
