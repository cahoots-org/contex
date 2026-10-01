"""CSVNodeParser must detect real CSV by structure, not "contains a comma".

Regression guard for the bug where every comma-containing source file (≈95% of
a code corpus) was routed to the CSV parser, shredded by DictReader, and either
stored as garbage row-dicts or dropped when the reader raised.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.node_converter import NodeConverter
from src.core.node_parsers import CSVNodeParser

nc = NodeConverter()

REAL_CSV = "id,name,score\n1,alice,9\n2,bob,7\n3,carol,8\n"
PYTHON = (
    "from pydantic import (\n    BaseModel,\n    Field,\n)\n\n"
    "class SourceType(str, Enum):\n    API = \"api\"\n    RSS = \"rss\"\n\n"
    "def build(name: str, kind: str) -> str:\n    return f\"{name},{kind}\"\n"
)
PROSE = (
    "The quick brown fox jumps over the lazy dog.\n"
    "It was the best of times, it was the worst of times.\n"
    "Call me Ishmael.\n"
)


def test_real_csv_is_csv():
    assert nc.parse(REAL_CSV, "text").format_name == "csv"


def test_python_is_not_csv():
    assert not CSVNodeParser().can_parse(PYTHON)
    assert nc.parse(PYTHON, "text").format_name != "csv"


def test_prose_with_commas_is_not_csv():
    # Comma counts per line differ (1, 2, 2) -> not a consistent table.
    assert not CSVNodeParser().can_parse(PROSE)


def test_explicit_hint_still_honored():
    assert CSVNodeParser().can_parse("anything at all", format_hint="csv")


if __name__ == "__main__":
    test_real_csv_is_csv()
    test_python_is_not_csv()
    test_prose_with_commas_is_not_csv()
    test_explicit_hint_still_honored()
    print("ok")
