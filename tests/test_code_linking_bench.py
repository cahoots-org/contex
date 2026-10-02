"""CI guard for the #221 claim: code parsing + linking beats the blob baseline.

Wraps the benchmark so the comparison is enforced on every run. The benchmark's
own assertions do the checking; this just makes failures show up in the suite.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark.code_linking_bench import run


def test_code_beats_paragraph_blob_baseline():
    code, base = run()
    assert code["cross_file_links"] > 0
    assert base["cross_file_links"] == 0
    assert code["isolated_definitions"] > base["isolated_definitions"]
    assert base["fragment_chunks"] > 0
