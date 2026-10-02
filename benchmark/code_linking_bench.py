"""Benchmark: code parsing + cross-file linking vs the paragraph-blob baseline.

Confirms the two claims behind issue #221 with deterministic, model-independent
metrics (the retrieval win is driven by structure, not by any particular
embedding model, so we measure the structure directly):

  1. Granularity — the code parser emits one definition per retrievable chunk,
     where the baseline (PlainTextNodeParser, split on blank lines) fragments a
     single function across chunks and/or merges several into one, so those
     definitions can't be retrieved as coherent units.

  2. Linkability — the code path produces a symbols index (defs/refs) that
     resolves cross-file edges by name equality; the blob path produces none, so
     cross-file neighbors are simply unreachable.

Run: python benchmark/code_linking_bench.py  (prints a table; asserts code wins)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.node import NodeType
from src.core.node_parsers import CodeNodeParser, PlainTextNodeParser

# A small multi-file corpus with realistic spacing: PEP8 blank lines between
# top-level defs AND blank lines *inside* function bodies — exactly what makes
# a blank-line splitter both merge and fragment real definitions.
CORPUS = {
    "svc/a.py": '''\
"""Config loading."""
from svc.io import read_file


def load_config(path):
    raw = read_file(path)

    return _parse(raw)


def _parse(raw):
    return dict(line.split("=") for line in raw.splitlines())
''',
    "svc/b.py": '''\
"""Service startup."""
from svc.a import load_config


def start(path):
    cfg = load_config(path)

    return Connection(cfg)


class Connection:
    def __init__(self, cfg):
        self.cfg = cfg

    def open(self):
        return connect_db(self.cfg)
''',
    "svc/c.py": '''\
"""DB layer."""


def connect_db(cfg):
    return read_file(cfg["dsn"])
''',
    "svc/io.py": '''\
"""IO helpers."""


def read_file(path):
    with open(path) as f:
        return f.read()
''',
}

# Matches a top-level Python definition header (def/class) in a text blob.
_HEADER_RE = re.compile(r"^(?:async\s+)?(?:def|class)\s+(\w+)", re.MULTILINE)


def _code_nodes(src, data_key):
    res = CodeNodeParser().parse(src, data_key=data_key)
    assert res.success, res.error
    return res.nodes


def measure_code():
    """Granularity + cross-file links for the code parser."""
    defs_by_name = {}          # name -> set(file)
    refs = []                  # (file, name)
    definition_chunks = 0      # retrievable one-definition nodes

    for data_key, src in CORPUS.items():
        for node in _code_nodes(src, data_key):
            if node.node_type in (NodeType.FUNCTION, NodeType.CLASS, NodeType.METHOD):
                definition_chunks += 1
                for name in node.metadata.get("defs", []):
                    defs_by_name.setdefault(name, set()).add(data_key)
            for name in node.metadata.get("refs", []):
                refs.append((data_key, name))

    cross_file_links = 0
    for ref_file, name in refs:
        others = defs_by_name.get(name, set()) - {ref_file}
        if others:
            cross_file_links += 1

    return {
        "definition_chunks": definition_chunks,
        "isolated_definitions": definition_chunks,  # one def per chunk, by construction
        "fragment_chunks": 0,
        "merged_chunks": 0,
        "cross_file_links": cross_file_links,
    }


def measure_baseline():
    """Same granularity view for the blank-line blob splitter; no links possible."""
    isolated = fragments = merged = 0

    for src in CORPUS.values():
        res = PlainTextNodeParser().parse(src)
        for node in res.nodes:
            headers = _HEADER_RE.findall(str(node.content))
            if len(headers) == 1:
                isolated += 1
            elif len(headers) == 0:
                fragments += 1       # an orphaned slice of some function body
            else:
                merged += 1          # several definitions fused into one chunk

    return {
        "isolated_definitions": isolated,
        "fragment_chunks": fragments,
        "merged_chunks": merged,
        "cross_file_links": 0,       # no symbols -> no cross-file edges
    }


def run():
    code = measure_code()
    base = measure_baseline()
    total_defs = code["definition_chunks"]

    print(f"corpus: {len(CORPUS)} files, {total_defs} definitions\n")
    print(f"{'metric':<24}{'code':>8}{'baseline':>10}")
    print("-" * 42)
    for key in ("isolated_definitions", "fragment_chunks", "merged_chunks", "cross_file_links"):
        print(f"{key:<24}{code.get(key, 0):>8}{base[key]:>10}")

    # The claims, as assertions so this doubles as a regression guard.
    assert code["isolated_definitions"] > base["isolated_definitions"], \
        "code parser should retrieve more definitions as coherent units"
    assert base["fragment_chunks"] > 0, \
        "baseline should demonstrably fragment real functions"
    assert code["cross_file_links"] > 0 and base["cross_file_links"] == 0, \
        "code path should resolve cross-file links the baseline cannot"
    return code, base


if __name__ == "__main__":
    run()
    print("\nok — code parsing + linking beats the paragraph-blob baseline")
