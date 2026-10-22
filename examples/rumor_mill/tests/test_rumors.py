import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from examples.rumor_mill import rumors as rumors_mod
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.feed import Event
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.rumors import TARGET_STATES, Rumors
from examples.rumor_mill.scenes import Scene
from examples.rumor_mill.tests.helpers import PID, StubBaselines, StubContex
from examples.rumor_mill.town import NEWCOMER

URL = os.environ.get("RUMOR_MILL_CONTEX_URL")
AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def hop(uid, speaker="Bert", text="the miller waters his flour"):
    return Utterance(uid, "v1", speaker, "tavern", text, "w-1")


async def make(results=None, error=None, day=100.0):
    contex = StubContex(results, error)
    rumors = Rumors(contex, StubBaselines(0.5), Config(session="s1", day_seconds=day, on_topic_margin=0.02))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta", player=PID)
    return rumors, contex


@pytest.mark.asyncio
async def test_add_publishes_the_words_only():
    _, contex = await make()
    assert contex.published == [
        ("rumor-mill:s1:rumors", "rumor:w-1", {"id": "w-1", "text": "the miller waters his flour"}),
    ]


@pytest.mark.asyncio
async def test_on_topic_clears_the_whisper_baseline_plus_margin():
    rumors, contex = await make({
        "close": [Match("rumor:w-2", {}, 0.9), Match("rumor:w-1", {}, 0.53)],
        "near": [Match("rumor:w-1", {}, 0.51)],
        "other": [Match("rumor:w-2", {}, 0.9)],
    })
    assert await rumors.on_topic("w-1", "close")
    assert not await rumors.on_topic("w-1", "near")
    assert not await rumors.on_topic("w-1", "other")
    project, _, kwargs = contex.queries[0]
    assert project == "rumor-mill:s1:rumors" and kwargs["threshold"] == 0
    assert kwargs["since"] == (AT - timedelta(seconds=5)).isoformat()


@pytest.mark.asyncio
async def test_a_failed_check_is_off_topic_and_logs_ids_only(caplog):
    rumors, _ = await make(error=RuntimeError("down"))
    with caplog.at_level(logging.WARNING):
        assert not await rumors.on_topic("w-1", "close")
    assert "w-1" in caplog.text and "0." not in caplog.text


class SlowBaselines:
    """Measures 0.5, but only once released; fails the first measure when told to."""

    def __init__(self, fail_first=False):
        self.release = asyncio.Event()
        self.fail_first = fail_first
        self.calls = 0

    async def measure(self, text):
        self.calls += 1
        await self.release.wait()
        if self.fail_first and self.calls == 1:
            raise RuntimeError("down")
        return 0.5


@pytest.mark.asyncio
async def test_a_hop_checked_before_the_baseline_waits_for_it():
    contex = StubContex({"close": [Match("rumor:w-1", {}, 0.53)]})
    baselines = SlowBaselines()
    rumors = Rumors(contex, baselines, Config(session="s1", on_topic_margin=0.02))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta", player=PID)
    check = asyncio.create_task(rumors.on_topic("w-1", "close"))
    await asyncio.sleep(0.01)
    assert not check.done()
    baselines.release.set()
    assert await check
    assert not rumors.record("w-1", hop("u-1"), {"v2": "Ann"}, True)
    assert rumors.cards(PID)[0]["drifted"] is False


@pytest.mark.asyncio
async def test_a_cancelled_check_leaves_the_baseline_for_the_next():
    contex = StubContex({"close": [Match("rumor:w-1", {}, 0.53)]})
    baselines = SlowBaselines()
    rumors = Rumors(contex, baselines, Config(session="s1", on_topic_margin=0.02))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta", player=PID)
    first = asyncio.create_task(rumors.on_topic("w-1", "close"))
    await asyncio.sleep(0.01)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    baselines.release.set()
    assert await rumors.on_topic("w-1", "close")
    assert baselines.calls == 1


@pytest.mark.asyncio
async def test_close_cancels_a_pending_measure():
    rumors = Rumors(StubContex(), SlowBaselines(), Config(session="s1", on_topic_margin=0.02))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta", player=PID)
    measuring = rumors._cards["w-1"].measuring
    rumors.close()
    await asyncio.gather(measuring, return_exceptions=True)
    assert measuring.cancelled()


@pytest.mark.asyncio
async def test_a_failed_baseline_is_measured_again_by_the_next_check():
    contex = StubContex({"close": [Match("rumor:w-1", {}, 0.53)]})
    baselines = SlowBaselines(fail_first=True)
    baselines.release.set()
    rumors = Rumors(contex, baselines, Config(session="s1", on_topic_margin=0.02))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta", player=PID)
    assert await rumors.on_topic("w-1", "close")
    assert baselines.calls == 2


@pytest.mark.asyncio
async def test_a_drift_on_a_reply_thread_shows_on_the_conversation_card():
    rumors, _ = await make()
    await rumors.add("w-2", "and the baker too", AT, "v1", "Marta", conversation="w-1", player=PID)
    assert rumors.record("w-2", hop("u-1", "Ann", "the weather"), {"v2": "Ann"}, False)
    assert not rumors.record("w-2", hop("u-2", "Ann", "the weather"), {"v3": "Cal"}, False)
    card, = rumors.cards(PID)
    assert card["drifted"] is True


@pytest.mark.asyncio
async def test_tallies(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rumors_mod.time, "time", lambda: now[0])
    rumors, _ = await make(day=100.0)
    assert not rumors.record("w-1", hop("u-1"), {"v2": "Ann", "v3": "Cal"}, True)
    now[0] = 1150.0
    assert not rumors.record("w-1", hop("u-2", "Ann"), {"v3": "Cal", "v4": "Dee"}, True)
    assert rumors.record("w-1", hop("u-3", "Cal", "the weather"), {"v5": "Eve"}, False)
    assert not rumors.record("w-1", hop("u-4", "Eve", "the weather again"), {"v6": "Fay"}, False)
    card, = rumors.cards(PID)
    assert (card["uid"], card["text"], card["target_id"], card["target_name"]) == (
        "w-1", "the miller waters his flour", "v1", "Marta",
    )
    assert [h["uid"] for h in card["hops"]] == ["u-1", "u-2"]
    assert card["hops"][1] == {
        "uid": "u-2", "speaker_id": "v1", "speaker": "Ann", "listeners": ["Cal", "Dee"], "ids": ["v3", "v4"],
        "place": "tavern", "text": "the miller waters his flour",
    }
    assert (card["heard"], card["still"], card["drifted"]) == (4, 2, True)


@pytest.mark.asyncio
async def test_a_reply_joins_its_conversation_card():
    rumors, _ = await make({"close": [Match("rumor:w-2", {}, 0.9)]})
    rumors.add_line("w-1", Utterance("u-1", "v1", "Marta", "tavern", "Who told you that?", "w-1"))
    await rumors.add("w-2", "the baker", AT, "v1", "Marta", conversation="w-1", player=PID)
    assert rumors.record("w-2", hop("u-2"), {"v2": "Ann"}, True) is False
    card, = rumors.cards(PID)
    assert card["uid"] == "w-1"
    assert card["conversation"] == [
        {"uid": "u-1", "speaker_id": "v1", "speaker": "Marta", "text": "Who told you that?"},
        {"uid": "w-2", "speaker_id": NEWCOMER, "speaker": "A newcomer", "text": "the baker"},
    ]
    assert [h["uid"] for h in card["hops"]] == ["u-2"]
    assert await rumors.on_topic("w-2", "close")


@pytest.mark.skipif(not URL, reason="RUMOR_MILL_CONTEX_URL not set")
@pytest.mark.asyncio
async def test_live_paraphrase_stays_on_topic_and_a_reply_does_not(tmp_path, caplog):
    cfg = Config(contex_url=URL, session=f"rumor-{uuid.uuid4().hex[:8]}", seed_path=str(tmp_path / "seed.json"))
    whisper = "The miller waters down his flour and sells it short to the whole village."
    paraphrase = "They say the miller has been thinning his flour with water and cheating everyone."
    reply = "I don't know about that, but my back has been aching since the frost."
    async with ContexClient(URL) as client:
        rumors = Rumors(client, Baselines(client, cfg), cfg)
        await rumors.add("w-1", whisper, datetime.now(UTC), "v1", "Marta", player=PID)
        with caplog.at_level(logging.DEBUG, logger=rumors_mod.__name__):
            assert await rumors.on_topic("w-1", paraphrase)
            assert not await rumors.on_topic("w-1", reply)
    print("\n".join(r.getMessage() for r in caplog.records))


@pytest.mark.asyncio
async def test_a_topic_is_checked_but_never_shown():
    rumors, contex = await make({"close": [Match("rumor:u-5", {}, 0.53)]})
    await rumors.add_topic("u-5", "the baker skims the till", AT)
    assert await rumors.on_topic("u-5", "close")
    assert contex.published[-1] == ("rumor-mill:s1:rumors", "rumor:u-5", {"id": "u-5", "text": "the baker skims the till"})
    assert [c["uid"] for c in rumors.cards(PID)] == ["w-1"]


@pytest.mark.asyncio
async def test_carries_names_only_on_topic_hops():
    rumors, _ = await make()
    await rumors.add("w-2", "reply", AT, "v1", "Marta", conversation="w-1", player=PID)
    rumors.record("w-1", hop("u-1"), {"v2": "Ann"}, True)
    rumors.record("w-2", hop("u-2"), {"v2": "Ann"}, True)
    rumors.record("w-1", hop("u-3"), {"v2": "Ann"}, False)
    assert rumors.carries("w-1", "u-1") and rumors.carries("w-2", "u-2") and rumors.carries("w-2", "u-1")
    assert not rumors.carries("w-1", "u-3")
    assert not rumors.carries("missing", "u-1")


@pytest.mark.asyncio
async def test_cards_list_only_the_players_own_rumors():
    rumors = Rumors(StubContex(), StubBaselines(0.5), Config(session="s1"))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta", player="p-a")
    await rumors.add("w-2", "the baker skims the till", AT, "v2", "Bert", player="p-b")
    assert [c["uid"] for c in rumors.cards("p-a")] == ["w-1"]
    assert [c["uid"] for c in rumors.cards("p-b")] == ["w-2"]


def status(state, vid="v1", name="Marta", **extra):
    return Event(1, 0.0, "whisper_status", "", {"whisper_id": "w-1", "villager_id": vid, "name": name, "state": state, **extra},
                 PID)


def said(uid, speaker, name, text="the miller waters his flour", place="tavern"):
    return Utterance(uid, speaker, name, place, text, "w-1")


def frontier(rumors):
    return {e["id"]: e for e in rumors.cards(PID)[0]["frontier"]}


@pytest.mark.asyncio
async def test_a_hop_makes_its_teller_pass_it_on_and_its_listener_hear_it():
    rumors, _ = await make()
    rumors.on_event(status("checking"))
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert"}, True)
    card, = rumors.cards(PID)
    assert [(e["id"], e["state"], e["told"]) for e in card["frontier"]] == [("v1", "passed", ["Bert"]), ("v2", "heard", [])]
    assert card["hops"][0]["speaker_id"] == "v1" and card["hops"][0]["ids"] == ["v2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [*TARGET_STATES, "not_carried"])
async def test_target_outcomes_follow_the_whisper_status(state):
    rumors, _ = await make()
    rumors.on_event(status("heard"))
    rumors.on_event(status(state, until=5.0) if state == "busy" else status(state))
    if state == "not_carried":
        assert frontier(rumors) == {}
    else:
        entry = frontier(rumors)["v1"]
        assert (entry["name"], entry["state"]) == ("Marta", TARGET_STATES[state])
        assert entry["until"] == (5.0 if state == "busy" else None)


@pytest.mark.asyncio
async def test_a_room_hop_is_one_summary_on_its_teller():
    rumors, _ = await make()
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert", "v3": "Cal", "v4": "Dee"}, True)
    rumors.record("w-1", said("u-2", "v3", "Cal"), {"v5": "Eve"}, True)
    entries = frontier(rumors)
    assert entries["v1"]["crowd"] == {"place": "tavern", "heard": 3, "passed": 1}
    assert "v2" not in entries and "v4" not in entries
    assert entries["v3"]["crowd"] is None and entries["v5"]["state"] == "heard"
    assert set(rumors.cards(PID)[0]["touched"]) == {"v1", "v2", "v3", "v4", "v5"}


@pytest.mark.asyncio
async def test_a_late_check_never_undoes_an_outcome():
    rumors, _ = await make()
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert"}, True)
    rumors.reached("w-1", "v2", "Bert", "kept")
    rumors.reached("w-1", "v2", "Bert", "busy", until=5.0)
    assert frontier(rumors)["v2"]["state"] == "kept"
    rumors.record("w-1", said("u-2", "v3", "Cal"), {"v2": "Bert"}, True)
    assert frontier(rumors)["v2"]["state"] == "heard"
    rumors.reached("w-1", "v1", "Marta", "uncared")
    rumors.record("w-1", said("u-3", "v3", "Cal"), {"v1": "Marta"}, True)
    assert frontier(rumors)["v1"]["state"] == "passed"


def heard_back(rumors):
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert"}, True)
    rumors.record("w-1", said("u-2", "v3", "Cal"), {"v2": "Bert"}, True)
    rumors.reached("w-1", "v2", "Bert", "uncared")


def kept_by_two(rumors):
    for uid, vid, name in (("u-1", "v2", "Bert"), ("u-2", "v3", "Ida")):
        rumors.record("w-1", said(uid, "v1", "Marta"), {vid: name}, True)
        rumors.reached("w-1", vid, name, "kept")


def told_bert(then):
    def scenario(rumors):
        rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert"}, True)
        then(rumors)
    return scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario, line", [
    (told_bert(lambda r: r.reached("w-1", "v2", "Bert", "uncared")),
     "It stopped with Bert: it didn't touch anything they care about."),
    (kept_by_two, "It stopped with Bert and Ida, who kept it quiet."),
    (told_bert(lambda r: r.reached("w-1", "v2", "Bert", "worn", depth=5)), "It wore out after 5 tellings."),
    (heard_back, "Everyone who heard it had heard it before."),
    (lambda r: r.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert", "v3": "Cal", "v4": "Dee"}, True),
     "It stopped at the tavern: 3 heard it and no one passed it on."),
    (told_bert(lambda r: r.reached("w-1", "v2", "Bert", "warned")), "It stopped with Bert, who warned others about you."),
    (told_bert(lambda r: r.record("w-1", said("u-2", "v2", "Bert", "the weather"), {"v3": "Cal"}, False)),
     "It turned into another story with Bert."),
])
async def test_stopped_says_why(scenario, line):
    rumors, _ = await make()
    scenario(rumors)
    assert rumors.cards(PID)[0]["stopped"] == line


@pytest.mark.asyncio
async def test_no_stop_line_while_anyone_still_holds_it(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rumors_mod.time, "time", lambda: now[0])
    rumors, _ = await make(day=100.0)
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert"}, True)
    assert rumors.cards(PID)[0]["stopped"] is None
    now[0] = 1101.0
    assert rumors.cards(PID)[0]["stopped"] == "It stopped with Bert, who kept it quiet."


@pytest.mark.asyncio
async def test_a_scene_marks_its_people_until_it_ends():
    rumors, _ = await make()
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert"}, True)
    rumors.reached("w-1", "v2", "Bert", "kept")
    scene = Scene("s-1", "tavern", "w-1", "w-1", True, "u-1", {"v2", "v3"}, {"v2"}, ["u-1"], 0.5)
    rumors.in_scene("w-1", scene, {"v2": "Bert", "v3": "Cal"})
    card, = rumors.cards(PID)
    entries = {e["id"]: e for e in card["frontier"]}
    assert entries["v2"]["scene"] == {"tone": "argument", "place": "tavern"}
    assert entries["v3"]["state"] == "heard" and entries["v3"]["scene"] == {"tone": "argument", "place": "tavern"}
    assert card["touched"]["v2"] == "scene" and card["stopped"] is None
    scene.ended = "cooled"
    rumors.in_scene("w-1", scene, {"v2": "Bert", "v3": "Cal"})
    assert all(e["scene"] is None for e in rumors.cards(PID)[0]["frontier"])


@pytest.mark.asyncio
async def test_frontier_entries_carry_no_internal_fields():
    rumors, _ = await make()
    rumors.on_event(status("heard"))
    rumors.record("w-1", said("u-1", "v1", "Marta"), {"v2": "Bert", "v3": "Cal"}, True)
    rumors.record("w-1", said("u-2", "v2", "Bert"), {"v4": "Dee"}, True)
    rumors.reached("w-1", "v4", "Dee", "worn", depth=2)
    for entry in rumors.cards(PID)[0]["frontier"]:
        assert set(entry) == {"id", "name", "state", "until", "told", "crowd", "scene"}


class StubOverhear:
    def __init__(self, claims):
        self.result = claims
        self.calls = []

    async def claims(self, text):
        self.calls.append(text)
        return self.result


@pytest.mark.asyncio
async def test_a_whisper_card_keeps_its_place_and_claims_and_a_reply_asks_nothing():
    overhear = StubOverhear(["tavern-ghost"])
    rumors = Rumors(StubContex(), StubBaselines(0.5), Config(session="s1"), overhear)
    await rumors.add("w-1", "a ghost in the tavern", AT, "v1", "Marta", player=PID, place="tavern")
    await rumors.add("w-2", "it moans", AT, "v1", "Marta", conversation="w-1", player=PID, place="tavern")
    assert overhear.calls == ["a ghost in the tavern"]
    card, = rumors.cards(PID)
    assert card["claims"] == ["tavern-ghost"] and card["place"] == "tavern"


@pytest.mark.asyncio
async def test_card_follows_a_reply_to_its_conversation_card():
    rumors, _ = await make()
    await rumors.add("w-2", "the baker", AT, "v1", "Marta", conversation="w-1", player=PID)
    assert rumors.card("w-2").uid == "w-1" and rumors.card("w-1").uid == "w-1"
    assert rumors.card("w-9") is None
