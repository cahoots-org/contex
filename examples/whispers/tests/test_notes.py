import re
from pathlib import Path

from examples.whispers.feed import Feed
from examples.whispers.lineage import Lineage
from examples.whispers.notes import RANK, Notes
from examples.whispers.tests.helpers import PID
from examples.whispers.town import Town


def make():
    feed = Feed(Lineage(), Town(), out=None)
    return feed, Notes(feed, PID)


def test_status_never_moves_backwards():
    feed, notes = make()
    assert notes.get("v1") == {"status": "none", "hints": [], "passed": []}
    feed.on_whisper_status("w-1", "v1", "Bert", "checking")
    assert notes.get("v1")["status"] == "listening"
    feed.on_whisper_status("w-1", "v1", "Bert", "kept")
    feed.on_whisper_status("w-2", "v1", "Bert", "heard")
    feed.on_whisper_status("w-3", "v1", "Bert", "brushed_off")
    feed.on_whisper_status("w-3", "v1", "Bert", "brushed_off", hint="The well worries me.")
    assert notes.get("v1") == {"status": "kept", "hints": ["The well worries me."], "passed": []}
    feed.on_whisper_status("w-1", "v1", "Bert", "took_the_bait", told=2)
    feed.on_whisper_status("w-4", "v1", "Bert", "kept")
    feed.on_whisper_status("w-5", "v1", "Bert", "not_carried")
    feed.on_whisper_status("w-6", "v1", "Bert", "brushed_off", hint=None)
    assert notes.get("v1") == {"status": "took_the_bait", "hints": ["The well worries me."], "passed": []}


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
        "v1": {"status": "brushed_off", "hints": ["Bread is dear."], "passed": []},
        "v2": {"status": "took_the_bait", "hints": [], "passed": []},
        "v3": {"status": "wary", "hints": [], "passed": []},
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


def test_every_hint_is_kept_once_in_order():
    feed, notes = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off", hint="Bread is dear.")
    feed.on_whisper_status("w-2", "v1", "Bert", "near_miss", hint="Go on, then.")
    feed.on_whisper_status("w-3", "v1", "Bert", "brushed_off", hint="Bread is dear.")
    feed.on_whisper_status("w-4", "v1", "Bert", "brushed_off", hint=None)
    assert notes.get("v1")["hints"] == ["Bread is dear.", "Go on, then."]
    assert Notes(feed, PID).all() == notes.all()


def test_what_they_passed_on_comes_from_bait_and_hops():
    feed, notes = make()
    feed.emit("whisper", player=PID, uid="w-1", text="the well is cursed")
    feed.on_whisper_status("w-1", "v1", "Bert", "took_the_bait", told=1)
    feed.on_whisper_status("w-1", "v1", "Bert", "took_the_bait", told=2)
    feed.emit("hop", player=PID, uid="u-1", root="w-1", speaker_id="v2", text="they say the well is cursed")
    feed.emit("hop", player=PID, uid="u-2", root="w-1", speaker_id="v2", text="the well, cursed")
    assert notes.get("v1") == {"status": "took_the_bait", "hints": [], "passed": ["the well is cursed"]}
    assert notes.get("v2") == {"status": "none", "hints": [], "passed": ["the well is cursed"]}
    assert Notes(feed, PID).all() == notes.all()


def test_an_answer_from_asking_around_is_a_hint():
    feed, notes = make()
    feed.emit("asked", player=PID, villager_id="v1", name="Bert", topic="the mill", line="Tom said so at the mill.")
    assert notes.get("v1") == {"status": "none", "hints": ["Tom said so at the mill."], "passed": []}
