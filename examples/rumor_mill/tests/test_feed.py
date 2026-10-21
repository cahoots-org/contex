from examples.rumor_mill.feed import REPLAY_EVENTS, Feed
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.town import Town


def make():
    town, lines, lineage = Town(), [], Lineage()
    for vid, name in (("v1", "Bert"), ("v2", "Marta")):
        town.add(Villager(vid, name, "baker", "calm", "none", [], {}))
    lineage.record("w-1", None, player=True)
    lineage.record("u-1", "w-1")
    lineage.record("u-2", None)
    return Feed(lineage, town, out=lines.append), lines


def utt(uid):
    return Utterance(uid, "v1", "Bert", "tavern", "the miller waters his flour", "w-1")


def test_only_player_rooted_deliveries_print():
    feed, lines = make()
    feed.on_delivery(utt("u-2"), ["v2"])
    assert lines == []
    feed.on_delivery(utt("u-1"), ["v2"])
    assert len(lines) == 1
    whisper = Utterance("w-1", "newcomer", "The newcomer", "tavern", "psst", None, "whispered to you")
    feed.on_delivery(whisper, ["v2"])
    assert len(lines) == 1


def test_single_and_group_line_formats():
    feed, lines = make()
    feed.on_delivery(utt("u-1"), ["v2"])
    feed.on_delivery(utt("u-1"), ["v2", "v1", "v3", "v4"])
    assert lines == [
        'Bert told Marta: "the miller waters his flour"',
        'Bert said at the tavern (heard by 4): "the miller waters his flour"',
    ]



def test_events_carry_ids_depth_and_listener_names():
    feed, lines = make()
    feed.lineage.record("u-3", "u-1")
    whisper = Utterance("w-1", "newcomer", "The newcomer", "tavern", "psst", None, "whispered to you")
    feed.on_delivery(whisper, ["v1"])
    feed.on_delivery(Utterance("u-3", "v2", "Marta", "well", "the miller drowns flour", "u-1"), ["v1"])
    w, hop = feed.recent
    assert (w.kind, w.data["uid"], w.data["target"], w.data["text"]) == ("whisper", "w-1", "Bert", "psst")
    assert hop.kind == "hop" and hop.id > w.id and hop.at >= w.at
    assert (hop.data["parent"], hop.data["root"], hop.data["depth"]) == ("u-1", "w-1", 2)
    assert (hop.data["listeners"], hop.data["heard"]) == (["Bert"], 1)
    assert hop.to_json()["kind"] == "hop" and "line" not in hop.to_json()
    assert lines == ['Marta told Bert: "the miller drowns flour"']


def test_unrooted_announcements_reach_sinks_but_not_the_terminal():
    feed, lines = make()
    seen = []
    feed.sinks.append(seen.append)
    feed.on_delivery(Utterance("u-2", "v1", "Bert", "square", "Bert joined The Watch", None, "announced"), [])
    feed.on_delivery(utt("u-2"), ["v2"])
    assert [e.kind for e in seen] == ["announcement"]
    assert lines == []


def test_goal_events_and_replay_is_bounded():
    feed, lines = make()
    goal = Goal("cat", "the cat is a spy", "belief")
    feed.on_progress(goal, 0.42)
    feed.on_progress(goal, 0.6, (15, 25))
    feed.on_met(goal)
    assert [(e.data["percent"], e.data["met"]) for e in feed.recent] == [(42, False), (60, False), (100, True)]
    assert feed.recent[1].data["believers"] == 15 and feed.recent[1].data["needed"] == 25
    assert lines == ['Goal "the cat is a spy": 42%', 'Goal "the cat is a spy": 15 of 25 believe it',
                     'Goal met: "the cat is a spy"']
    for _ in range(REPLAY_EVENTS):
        feed.say("x")
    assert len(feed.recent) == REPLAY_EVENTS
    assert feed.recent[-1].id == REPLAY_EVENTS + 3


def test_refusal_event_and_terminal_lines():
    feed, lines = make()
    feed.on_refusal("v1", "Bert", "Not a word more.")
    feed.on_refusal("v2", "Marta", None)
    assert lines == ['Bert turns away from you: "Not a word more."', "Marta turns away from you."]
    first, second = feed.recent
    assert (first.kind, first.data) == ("refusal", {"name": "Bert", "villager_id": "v1", "line": "Not a word more."})
    assert second.data["line"] is None


def test_warning_event_has_names_and_no_line_or_score():
    feed, lines = make()
    feed.on_delivery(Utterance("u-9", "v1", "Bert", "tavern", "the newcomer is behind it", None, "warned"), ["v2"])
    assert lines == ["Bert warned Marta about you."]
    (event,) = feed.recent
    assert event.kind == "warning"
    assert event.data == {"speaker_id": "v1", "speaker": "Bert", "target_id": "v2", "target": "Marta"}


def test_terminal_prints_only_the_brush_off():
    feed, lines = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "checking")
    feed.on_whisper_status("w-1", "v1", "Bert", "busy", until=123.0)
    feed.on_whisper_status("w-1", "v1", "Bert", "heard")
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off")
    assert lines == []
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off", hint="Not now, the oven.")
    feed.on_whisper_status("w-2", "v1", "Bert", "brushed_off", hint=None)
    assert lines == ['Bert brushes you off: "Not now, the oven."', "Bert shrugs it off."]
    busy = feed.recent[1].to_json()
    assert busy["kind"] == "whisper_status"
    assert {k: busy[k] for k in ("whisper_id", "villager_id", "name", "state", "until")} == {
        "whisper_id": "w-1", "villager_id": "v1", "name": "Bert", "state": "busy", "until": 123.0,
    }


def test_a_resolved_whisper_never_moves_backwards():
    feed, _ = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "kept")
    feed.on_whisper_status("w-1", "v1", "Bert", "heard")
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off", hint="Not now.")
    feed.on_whisper_status("w-1", "v1", "Bert", "not_carried")
    assert [e.data["state"] for e in feed.recent] == ["kept"]


def test_a_brush_off_takes_its_hint_and_can_be_upgraded():
    feed, lines = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off")
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off", hint="Not now.")
    feed.on_whisper_status("w-1", "v1", "Bert", "took_the_bait", told=1)
    assert [e.data["state"] for e in feed.recent] == ["brushed_off", "brushed_off", "took_the_bait"]
    assert lines == ['Bert brushes you off: "Not now."']


def test_an_answered_whisper_never_moves_back_to_listening_or_brushed_off():
    feed, _ = make()
    feed.on_whisper_status("w-1", "v1", "Bert", "answered")
    feed.on_whisper_status("w-1", "v1", "Bert", "heard")
    feed.on_whisper_status("w-1", "v1", "Bert", "busy", until=1.0)
    feed.on_whisper_status("w-1", "v1", "Bert", "brushed_off", hint="Not now.")
    feed.on_whisper_status("w-1", "v1", "Bert", "moved_on")
    assert [e.data["state"] for e in feed.recent] == ["answered", "moved_on"]


def test_time_lines_name_the_day_at_dawn():
    feed, lines = make()
    feed.on_time(2, "morning")
    feed.on_time(2, "evening")
    assert lines == ["Day 2 dawns over the village.", "Evening falls, and lamps are lit."]
    assert [(e.kind, e.data["day"], e.data["phase"]) for e in feed.recent] == [("time", 2, "morning"), ("time", 2, "evening")]
