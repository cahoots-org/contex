import re
from pathlib import Path

from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.notes import RANK, Notes
from examples.rumor_mill.tests.helpers import PID
from examples.rumor_mill.town import Town


def make():
    feed = Feed(Lineage(), Town(), out=None)
    return feed, Notes(feed, PID)


def test_status_never_moves_backwards():
    feed, notes = make()
    assert notes.get("v1") == {"status": "none", "hint": None}
    feed.on_whisper_status("w-1", "v1", "Bert", "checking")
    assert notes.get("v1")["status"] == "listening"
    feed.on_whisper_status("w-1", "v1", "Bert", "kept")
    feed.on_whisper_status("w-2", "v1", "Bert", "heard")
    feed.on_whisper_status("w-3", "v1", "Bert", "brushed_off")
    feed.on_whisper_status("w-3", "v1", "Bert", "brushed_off", hint="The well worries me.")
    assert notes.get("v1") == {"status": "kept", "hint": "The well worries me."}
    feed.on_whisper_status("w-1", "v1", "Bert", "took_the_bait", told=2)
    feed.on_whisper_status("w-4", "v1", "Bert", "kept")
    feed.on_whisper_status("w-5", "v1", "Bert", "not_carried")
    feed.on_whisper_status("w-6", "v1", "Bert", "brushed_off", hint=None)
    assert notes.get("v1") == {"status": "took_the_bait", "hint": "The well worries me."}


def test_wary_overrides():
    feed, notes = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "took_the_bait", told=3)
    feed.on_whisper_status("w-2", "v1", "Bert", "warned")
    assert notes.get("v1")["status"] == "wary"
    feed.on_whisper_status("w-3", "v2", "Marta", "took_the_bait", told=1)
    feed.on_refusal("v2", "Marta", "Leave me be.")
    feed.on_whisper_status("w-4", "v2", "Marta", "kept")
    assert notes.get("v2")["status"] == "wary"


def test_replay_rebuilds_the_same_notes():
    feed, notes = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off", hint="Bread is dear.")
    feed.on_whisper_status("w-2", "v2", "Marta", "heard")
    feed.on_whisper_status("w-2", "v2", "Marta", "took_the_bait", told=2)
    feed.on_refusal("v3", "Ida")
    feed.say("unrelated")
    assert Notes(feed, PID).all() == notes.all()
    assert notes.all() == {
        "v1": {"status": "brushed_off", "hint": "Bread is dear."},
        "v2": {"status": "took_the_bait", "hint": None},
        "v3": {"status": "wary", "hint": None},
    }


def test_notes_rank_answered():
    feed, notes = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "heard")
    feed.on_whisper_status("w-1", "v1", "Bert", "answered")
    assert notes.get("v1")["status"] == "answered"
    feed.on_whisper_status("w-1", "v1", "Bert", "moved_on")
    assert notes.get("v1")["status"] == "kept"
    feed.on_whisper_status("w-2", "v1", "Bert", "answered")
    assert notes.get("v1")["status"] == "kept"
    assert RANK.index("listening") < RANK.index("answered") < RANK.index("kept")
    page = (Path(__file__).parents[1] / "static" / "index.html").read_text()
    assert re.search(r"const RANK = \[(.*?)\];", page).group(1) == ", ".join(f'"{r}"' for r in RANK)


def test_notes_hold_only_the_players_own_villagers():
    feed, _ = make()
    feed.lineage.record("w-1", None, player="p-a")
    feed.lineage.record("w-2", None, player="p-b")
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off")
    feed.on_whisper_status("w-2", "v2", "Marta", "took_the_bait", told=1)
    assert list(Notes(feed, "p-a").all()) == ["v1"]
