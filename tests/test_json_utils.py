"""Unit tests for JSON serialization helpers (pure — no DB or model deps)."""
import datetime
import json
from decimal import Decimal

from src.core.json_utils import json_safe_default


def _dumps(obj):
    return json.dumps(obj, default=json_safe_default)


def test_date():
    assert _dumps({"d": datetime.date(2026, 9, 21)}) == '{"d": "2026-09-21"}'


def test_datetime():
    dt = datetime.datetime(2026, 9, 21, 13, 30, 0)
    assert json.loads(_dumps({"t": dt}))["t"] == "2026-09-21T13:30:00"


def test_time():
    assert json.loads(_dumps({"t": datetime.time(9, 5)}))["t"] == "09:05:00"


def test_decimal():
    assert json.loads(_dumps({"n": Decimal("1.5")}))["n"] == 1.5


def test_set_becomes_list():
    assert sorted(json.loads(_dumps({"s": {1, 2, 3}}))["s"]) == [1, 2, 3]


def test_bytes():
    assert json.loads(_dumps({"b": b"hi"}))["b"] == "hi"


def test_nested_date():
    data = {"outer": [{"when": datetime.date(2026, 1, 1)}]}
    assert json.loads(_dumps(data))["outer"][0]["when"] == "2026-01-01"


def test_unknown_falls_back_to_str():
    class Foo:
        def __str__(self):
            return "foo!"

    assert json.loads(_dumps({"x": Foo()}))["x"] == "foo!"
