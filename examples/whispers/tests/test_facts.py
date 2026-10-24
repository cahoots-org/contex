import json
from examples.whispers import facts
from examples.whispers.facts import Facts
from examples.whispers.models import Utterance


def test_recent_lists_lines_said_at_a_place_since_a_time(monkeypatch):
    kept = Facts()
    monkeypatch.setattr(facts.time, "time", lambda: 100.0)
    kept.record(Utterance("u-1", "v1", "Bert", "square", "old news", None))
    monkeypatch.setattr(facts.time, "time", lambda: 200.0)
    kept.record(Utterance("u-2", "v1", "Bert", "square", "fresh news", None))
    kept.record(Utterance("u-3", "v1", "Bert", "tavern", "tavern news", None))
    kept.record(Utterance("u-4", "v2", "Ada", "square", "later news", None))
    assert [u.id for u in kept.recent("square", 150)] == ["u-2", "u-4"]
    assert kept.get("u-1").text == "old news"


def test_state_round_trips_as_json_in_order(monkeypatch):
    kept = Facts()
    monkeypatch.setattr(facts.time, "time", lambda: 100.0)
    kept.record(Utterance("u-2", "v1", "Bert", "square", "news", "u-1", names_newcomer=True, about="p-1"))
    kept.record(Utterance("u-1", "v2", "Ada", "tavern", "more", None))
    fresh = Facts()
    fresh.restore(json.loads(json.dumps(kept.to_state())))
    assert list(fresh._by_id.items()) == list(kept._by_id.items())
