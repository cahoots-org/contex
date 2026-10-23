import asyncio
import logging
import random
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill.actions import ANSWER, IGNORE, SPEAK, WARN
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision, _state
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.game import Game
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import Mind, mind_project, said_project
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.overhear import Overhear
from examples.rumor_mill.player import Player
from examples.rumor_mill.players import Players
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.scenes import SCENE_SPEECH
from examples.rumor_mill.tests.helpers import (
    PID, TIMEOUT, Always, FakeMind, StubBaselines, StubClient, StubContex, make, stub_player, wait_for,
)
from examples.rumor_mill.town import NEWCOMER, Town
from examples.rumor_mill.usage import CountingJev, Usage
from examples.rumor_mill.writer import Writer

SETTLE = 0.1


class RecordingFeed(Feed):
    def __init__(self):
        super().__init__(Lineage(), Town(), out=None)
        self.deliveries = []

    def on_delivery(self, utterance, listener_ids, on_topic=True, scene=None):
        self.deliveries.append((utterance.id, listener_ids))


class Turns:
    def __init__(self, fail_first=False, leave=False):
        self.views = []
        self.acts = []
        self.fail_first = fail_first
        self.leave = leave
        self.strength = None

    async def decide(self, jev, view):
        self.views.append(view)
        if self.fail_first and len(self.views) == 1:
            raise RuntimeError("jev down")
        return Decision(IGNORE, None, None, None, self.strength)

    async def act(self, decision, villager, *, town, **kw):
        self.acts.append((villager.id, kw["inspiration"]))
        if self.leave:
            town.remove(villager.id)


@pytest.fixture
def turns(monkeypatch):
    t = Turns()
    monkeypatch.setattr(engine_mod, "decide", t.decide)
    monkeypatch.setattr(engine_mod, "act", t.act)
    return t


async def setup(*vids, cooldown=0.05, game=None, rumors=None, contex=None, config=None):
    minds = {}

    def factory(v):
        minds[v.id] = FakeMind(v)
        return minds[v.id]

    feed = RecordingFeed()
    engine = Engine(
        config or Config(cooldown_seconds=cooldown), Town(), contex or StubContex(), None, None, Lineage(), Ids(), feed,
        mind_factory=factory, game=game, rumors=rumors,
    )
    engine.players.add(stub_player())
    for vid in vids:
        await engine.add_villager(make(vid))
    await engine.run()
    return engine, minds, feed


def push(mind, n=1):
    for _ in range(n):
        mind.queue.put_nowait(None)


@pytest.mark.asyncio
async def test_push_with_no_new_matches_makes_no_turn(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[]]
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 1)
    await asyncio.sleep(SETTLE)
    assert minds["v1"].results == []
    assert turns.views == []
    await engine.stop()


@pytest.mark.asyncio
async def test_pushes_during_cooldown_coalesce_into_one_turn(turns):
    engine, minds, _ = await setup("v1", cooldown=1.0)
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    push(minds["v1"], 3)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    await wait_for(lambda: len(turns.views) == 2)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 2
    assert minds["v1"].polls == 2
    await engine.stop()


@pytest.mark.asyncio
async def test_turn_exception_is_logged_and_worker_survives(turns, caplog):
    turns.fail_first = True
    engine, minds, _ = await setup("v1")
    with caplog.at_level(logging.ERROR):
        push(minds["v1"])
        await wait_for(lambda: "turn failed for v1" in caplog.text)
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    await engine.stop()


@pytest.mark.asyncio
async def test_departed_villager_worker_ends(turns):
    turns.leave = True
    engine, minds, _ = await setup("v1")
    push(minds["v1"])
    await wait_for(lambda: "v1" not in engine.minds)
    push(minds["v1"])
    await asyncio.sleep(SETTLE)
    assert len(turns.acts) == 1
    assert minds["v1"].polls == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_deliver_skips_departed_listeners_and_notifies_feed(turns):
    engine, minds, feed = await setup("v1", "v2", "v3")
    engine.town.remove("v3")
    utt = Utterance("u-1", "v1", "V1", "square", "hello", None)
    await engine.deliver(utt, ["v2", "v3"])
    assert minds["v2"].heard == [("utt:u-1", utt.to_data())]
    assert minds["v3"].heard == []
    assert feed.deliveries == [("u-1", ["v2"])]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_mind_hears_the_line_without_its_place(turns):
    engine, minds, _ = await setup("v1", "v2")
    await engine.deliver(Utterance("u-1", "v1", "V1", "well", "the bread is stale", None), ["v2"])
    assert minds["v2"].heard == [("utt:u-1", {"id": "u-1", "text": "V1 said: the bread is stale"})]
    await engine.stop()


@pytest.mark.asyncio
async def test_stop_leaves_no_pending_tasks(turns):
    engine, minds, _ = await setup("v1", "v2", game=Game())
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    await engine.stop()
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert pending == []


@pytest.mark.asyncio
async def test_remove_villager_enqueues_departure_once(turns):
    engine, _, _ = await setup("v1", "v2")
    await engine.remove_villager("v1")
    await engine.remove_villager("v1")
    assert engine.departures.qsize() == 1
    assert engine.departures.get_nowait() == "v1"
    await engine.stop()


@pytest.mark.asyncio
async def test_failed_open_leaves_town_and_engine_unchanged(turns):
    engine, _, _ = await setup("v1")

    class BrokenMind(FakeMind):
        async def open(self):
            raise RuntimeError("contex down")

    engine.mind_factory = BrokenMind
    with pytest.raises(RuntimeError):
        await engine.add_villager(make("v2"))
    assert engine.town.alive_count == 1
    assert [v.id for v in engine.town.villagers] == ["v1"]
    assert list(engine.minds) == ["v1"]
    assert list(engine._tasks) == ["v1"]
    await engine.stop()


class FailingMind(FakeMind):
    async def hear(self, key, data):
        raise RuntimeError("contex down")


@pytest.mark.asyncio
async def test_one_failing_listener_does_not_hide_the_others(turns, caplog):
    engine, minds, feed = await setup("v1", "v2", "v3")
    engine.minds["v3"] = FailingMind(engine.town.get("v3"))
    utt = Utterance("u-1", "v1", "V1", "square", "hello", None)
    with caplog.at_level(logging.ERROR):
        await engine.deliver(utt, ["v2", "v3"])
    assert minds["v2"].heard == [("utt:u-1", utt.to_data())]
    assert feed.deliveries == [("u-1", ["v2"])]
    assert "v3" in caplog.text
    await engine.stop()


@pytest.mark.asyncio
async def test_deliver_raises_when_every_listener_fails(turns):
    engine, _, feed = await setup("v1", "v2")
    engine.minds["v2"] = FailingMind(engine.town.get("v2"))
    utt = Utterance("u-1", "v1", "V1", "square", "hello", None)
    with pytest.raises(RuntimeError):
        await engine.deliver(utt, ["v2"])
    assert feed.deliveries == []
    await engine.stop()


@pytest.mark.asyncio
async def test_repeated_listen_failures_log_one_traceback(turns, monkeypatch, caplog):
    monkeypatch.setattr(engine_mod, "LISTEN_RETRY_SECONDS", 0.01)
    engine, minds, _ = await setup("v1")

    async def broken():
        raise RuntimeError("contex down")
        yield

    minds["v1"].pushes = broken
    with caplog.at_level(logging.WARNING):
        await wait_for(lambda: len(caplog.records) >= 3)
    await engine.stop()
    with_trace = [r for r in caplog.records if r.exc_info]
    assert len(with_trace) == 1
    assert all("v1" in r.getMessage() for r in caplog.records)


def utterance():
    return Utterance("u-1", "v1", "V1", "square", "watch the newcomer", None)


@pytest.mark.asyncio
async def test_addressed_message_wakes_a_villager_whose_needs_did_not_match(turns):
    engine, minds, _ = await setup("v1", "v2")
    minds["v2"].results = [[]]
    utt = utterance()
    await engine.deliver(utt, ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    view = turns.views[0]
    assert view.villager.id == "v2"
    assert view.new == [Match("utt:u-1", utt.to_data())]
    assert minds["v2"].heard == [("utt:u-1", utt.to_data())]
    await engine.stop()


@pytest.mark.asyncio
async def test_unaddressed_delivery_does_not_bypass_matching(turns):
    engine, minds, _ = await setup("v1", "v2")
    await engine.deliver(utterance(), ["v2"])
    await asyncio.sleep(SETTLE)
    assert minds["v2"].polls == 0
    assert turns.views == []
    assert engine._pending == {}
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_also_matched_by_poll_appears_once(turns):
    engine, minds, _ = await setup("v1", "v2")
    utt = utterance()
    minds["v2"].results = [[Match("utt:u-1", utt.to_data())]]
    await engine.deliver(utt, ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["utt:u-1"]
    assert turns.views[0].recent == []
    await engine.stop()


@pytest.mark.asyncio
async def test_pending_is_cleared_after_the_turn(turns):
    engine, minds, _ = await setup("v1", "v2")
    minds["v2"].results = [[], []]
    await engine.deliver(utterance(), ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert "v2" not in engine._pending
    push(minds["v2"])
    await wait_for(lambda: minds["v2"].polls == 2)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_during_cooldown_is_kept_for_the_next_turn(turns):
    engine, minds, _ = await setup("v1", "v2", cooldown=1.0)
    push(minds["v2"])
    await wait_for(lambda: len(turns.views) == 1)
    minds["v2"].results = [[]]
    await engine.deliver(utterance(), ["v2"], addressed=True)
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    assert [m.data_key for m in engine._pending["v2"]] == ["utt:u-1"]
    await wait_for(lambda: len(turns.views) == 2)
    assert [m.data_key for m in turns.views[1].new] == ["utt:u-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_to_a_departed_villager_is_dropped_quietly(turns):
    engine, minds, feed = await setup("v1", "v2")
    await engine.remove_villager("v2")
    await engine.deliver(utterance(), ["v2"], addressed=True)
    assert engine._pending == {}
    assert "v2" not in engine._wakes
    assert minds["v2"].heard == []
    assert feed.deliveries == [("u-1", [])]
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_listener_who_leaves_during_delivery_is_skipped(turns):
    engine, minds, feed = await setup("v1", "v2", "v3")
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_hear(key, data):
        started.set()
        await release.wait()

    minds["v2"].hear = slow_hear
    delivery = asyncio.create_task(engine.deliver(utterance(), ["v2", "v3"], addressed=True))
    await started.wait()
    await engine.remove_villager("v2")
    release.set()
    await delivery
    assert "v2" not in engine._pending
    assert feed.deliveries == [("u-1", ["v2", "v3"])]
    await wait_for(lambda: [v.villager.id for v in turns.views] == ["v3"])
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_message_survives_a_failed_poll(turns):
    engine, minds, _ = await setup("v1", "v2")
    mind = minds["v2"]
    real_poll = mind.poll
    mind.results = [[]]

    async def flaky_poll():
        if mind.polls == 0:
            mind.polls += 1
            raise RuntimeError("contex down")
        return await real_poll()

    mind.poll = flaky_poll
    await engine.deliver(utterance(), ["v2"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["utt:u-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_suspicion_strength_updates_scores_and_a_rating_error_does_not_fail_the_turn(turns, caplog):
    engine, minds, _ = await setup("v1")
    engine.lineage.record("w-1", None, player=PID)
    engine.lineage.record("u-0", "w-1")
    engine.facts.record(Utterance("u-0", "v2", "V2", "square", "news", "w-1", names_newcomer=True))
    turns.strength = 1.0
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    view = turns.views[0]
    assert view.names_newcomer == view.new
    assert (view.suspicion, view.warn_threshold) == (0.0, engine.config.warn_threshold)
    assert engine.suspicion.score("v1", PID) == engine.config.suspicion_hear_max

    def broken(*args):
        raise RuntimeError("rating down")

    engine.suspicion.rate = broken
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 2)
    assert turns.views[1].suspicion == engine.config.suspicion_hear_max
    assert "rating down" in caplog.text
    engine.suspect("v1", PID, 0.1, "was warned about the newcomer", "u-1")
    assert engine.suspicion.score("v1", PID) == pytest.approx(engine.config.suspicion_hear_max + 0.1)
    await engine.stop()


def test_default_minds_get_the_engine_baselines():
    config = Config()
    baselines = Baselines(None, config)
    engine = Engine(config, Town(), None, None, None, Lineage(), Ids(), RecordingFeed(), baselines=baselines)
    mind = engine.mind_factory(make("v1"))
    assert isinstance(mind, Mind) and mind.baselines is baselines


@pytest.mark.asyncio
async def test_hit_goes_to_the_next_turn_without_a_push(turns):
    engine, minds, _ = await setup("v1", cooldown=1.0)
    assert engine.cooldown_ends("v1") <= time.time()
    minds["v1"].results = [[]]
    hit = Match("utt:w-1", {"id": "w-1", "text": "psst"}, 0.8)
    engine.hear_directly("v1", hit)
    engine.hear_directly("v1", hit)
    await wait_for(lambda: len(turns.views) == 1)
    assert turns.views[0].new == [hit]
    assert minds["v1"].heard == []
    assert time.time() + 0.5 < engine.cooldown_ends("v1") <= time.time() + 1.0
    await engine.stop()


@pytest.mark.asyncio
async def test_target_leaving_before_the_turn_leaves_no_pending(turns):
    engine, minds, _ = await setup("v1", "v2", cooldown=1.0)
    push(minds["v2"])
    await wait_for(lambda: len(turns.views) == 1)
    engine.hear_directly("v2", Match("utt:w-1", {"id": "w-1"}, 0.8))
    assert [m.data_key for m in engine._pending["v2"]] == ["utt:w-1"]
    await engine.remove_villager("v2")
    assert engine._pending == {}
    assert engine.cooldown_ends("v2") <= time.time()
    engine.hear_directly("v2", Match("utt:w-2", {"id": "w-2"}, 0.8))
    assert engine._pending == {}
    await asyncio.sleep(SETTLE)
    assert len(turns.views) == 1
    await engine.stop()


def whisper_match():
    return Match("utt:w-1", {"id": "w-1", "text": "psst"}, 0.8)


@pytest.mark.asyncio
async def test_whisper_heard_directly_then_polled_reaches_one_turn(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[], [whisper_match()]]
    engine.hear_directly("v1", whisper_match())
    await wait_for(lambda: len(turns.views) == 1)
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 2)
    await asyncio.sleep(SETTLE)
    assert [[m.data_key for m in v.new] for v in turns.views] == [["utt:w-1"]]
    await engine.stop()


@pytest.mark.asyncio
async def test_whisper_polled_then_heard_directly_reaches_one_turn(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[whisper_match()], []]
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    engine.hear_directly("v1", whisper_match())
    await wait_for(lambda: minds["v1"].polls == 2)
    await asyncio.sleep(SETTLE)
    assert [[m.data_key for m in v.new] for v in turns.views] == [["utt:w-1"]]
    assert engine._pending.get("v1") is None
    await engine.stop()


@pytest.mark.asyncio
async def test_a_hit_while_a_turn_is_in_progress_reports_busy(turns, monkeypatch):
    engine, minds, _ = await setup("v1", cooldown=5.0)
    engine.contex = StubContex({"bread": [Match("utt:w-1", {}, 0.9)]})
    engine.baselines = StubBaselines(0.5)
    deciding, release = asyncio.Event(), asyncio.Event()

    async def slow_decide(jev, view):
        deciding.set()
        await release.wait()
        return Decision(IGNORE, None, None, None)

    monkeypatch.setattr(engine_mod, "decide", slow_decide)
    lineage = Lineage()
    feed = Feed(lineage, engine.town, out=None)
    player = Player(engine, engine.town, lineage, Ids(), feed, engine.config, player_id=PID)
    push(minds["v1"])
    await asyncio.wait_for(deciding.wait(), TIMEOUT)
    assert await player.whisper_to("v1", "psst") is None
    status = [e.data for e in feed.recent if e.kind == "whisper_status"][-1]
    assert status["state"] == "busy" and status["until"] > time.time() + 4
    release.set()
    await wait_for(lambda: engine.cooldown_ends("v1") > time.time() and "v1" not in engine._turning)
    await engine.stop()


@pytest.mark.asyncio
async def test_whisper_polled_then_forgotten_then_heard_directly_reaches_one_turn(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[whisper_match()], [], [whisper_match()]]
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    engine.hear_directly("v1", whisper_match())
    await wait_for(lambda: minds["v1"].polls == 2)
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 3)
    await asyncio.sleep(SETTLE)
    assert [[m.data_key for m in v.new] for v in turns.views] == [["utt:w-1"]]
    await engine.stop()


class Relays:
    """Each turn takes the next planned action; a speak relays the first new item to the planned listeners."""

    def __init__(self):
        self.plan = []

    async def decide(self, jev, view):
        action, listeners = self.plan.pop(0)
        return Decision(action, view.new[0], listeners, None)

    async def act(self, decision, villager, *, hub, lineage, ids, **kw):
        if decision.action != SPEAK:
            return
        uid = ids.next("u")
        lineage.record(uid, decision.source.data["id"])
        utterance = Utterance(uid, villager.id, villager.name, "square", "did you hear", decision.source.data["id"])
        await hub.deliver(utterance, decision.target)


@pytest.fixture
def relays(monkeypatch):
    r = Relays()
    monkeypatch.setattr(engine_mod, "decide", r.decide)
    monkeypatch.setattr(engine_mod, "act", r.act)
    return r


def statuses(feed):
    return [(e.data["whisper_id"], e.data["villager_id"], e.data["state"], e.data.get("told"))
            for e in feed.recent if e.kind == "whisper_status"]


def whisper(engine, vid, wid="w-1"):
    engine.lineage.record(wid, None, player=PID)
    engine.hear_directly(vid, Match(f"utt:{wid}", {"id": wid, "text": "psst"}, 0.8))


@pytest.mark.asyncio
async def test_turn_that_relays_reports_took_the_bait_with_count(relays):
    engine, minds, feed = await setup("v1", "v2", "v3")
    minds["v1"].results = [[]]
    relays.plan = [(SPEAK, ["v2", "v3"])]
    whisper(engine, "v1")
    await wait_for(lambda: statuses(feed))
    assert statuses(feed) == [("w-1", "v1", "took_the_bait", 2)]
    await engine.stop()


@pytest.mark.asyncio
async def test_turn_that_ignores_reports_kept_then_later_relay_upgrades(relays):
    engine, minds, feed = await setup("v1", "v2")
    minds["v1"].results = [[], [Match("utt:u-9", {"id": "u-9", "text": "the well"})]]
    engine.lineage.record("u-9", "w-1")
    relays.plan = [(IGNORE, None), (SPEAK, ["v2"])]
    whisper(engine, "v1")
    await wait_for(lambda: statuses(feed))
    assert statuses(feed) == [("w-1", "v1", "kept", None)]
    push(minds["v1"])
    await wait_for(lambda: len(statuses(feed)) == 2)
    assert statuses(feed)[1] == ("w-1", "v1", "took_the_bait", 1)
    await engine.stop()


@pytest.mark.asyncio
async def test_a_routine_move_that_carries_a_kept_whisper_takes_the_bait(relays, monkeypatch):
    engine, minds, feed = await setup("v1", "v2")
    minds["v1"].results = [[]]
    relays.plan = [(IGNORE, None)]
    whisper(engine, "v1")
    await wait_for(lambda: statuses(feed))

    async def carry(decision, villager, *, hub, lineage, **kw):
        lineage.record("u-c", decision.source.data["id"])
        line = Utterance("u-c", villager.id, villager.name, "square", "did you hear", decision.source.data["id"])
        await hub.deliver(line, ["v2"])

    monkeypatch.setattr(engine_mod, "act", carry)
    await engine._stroll("v1", "mill")
    assert statuses(feed) == [("w-1", "v1", "kept", None), ("w-1", "v1", "took_the_bait", 1)]
    assert "v1" not in engine._said
    await engine.stop()


@pytest.mark.asyncio
async def test_departure_resolves_whispers_still_being_heard(relays):
    engine, _, feed = await setup("v1", "v2")
    feed.on_whisper_status("w-1", "v1", "V1", "heard")
    feed.on_whisper_status("w-2", "v1", "V1", "busy", until=0.0)
    feed.on_whisper_status("w-3", "v1", "V1", "brushed_off")
    feed.on_whisper_status("w-4", "v2", "V2", "checking")
    await engine.remove_villager("v1")
    assert statuses(feed)[4:] == [("w-1", "v1", "kept", None), ("w-2", "v1", "kept", None)]
    feed.on_whisper_status("w-1", "v1", "V1", "heard")
    assert len(statuses(feed)) == 6
    await engine.stop()


class Rater:
    """A Jev that always ignores and is convinced the newcomer is behind it, when asked."""

    def __init__(self):
        self.asked = []

    async def system_one(self, state, questions):
        self.asked.append("suspicion" in questions)
        choices = {"action": SimpleNamespace(choice=IGNORE, probabilities={IGNORE: 1.0})}
        scores = {"suspicion": SimpleNamespace(score=3.0)} if "suspicion" in questions else {}
        return SimpleNamespace(choices=choices, scores=scores)


@pytest.mark.asyncio
async def test_hurtful_gossip_raises_no_suspicion(monkeypatch):
    async def no_act(*args, **kw):
        pass

    monkeypatch.setattr(engine_mod, "act", no_act)
    engine, minds, _ = await setup("v1")
    engine.jev = Rater()
    engine.lineage.record("w-1", None, player=PID)
    engine.lineage.record("u-0", "w-1")
    engine.facts.record(Utterance("u-0", "v2", "V2", "square", "Drogo drinks", "w-1", style="exaggerated"))
    minds["v1"].results = [[Match("utt:u-0", {"id": "u-0", "text": "V2 said: Drogo drinks"})]]
    push(minds["v1"])
    await wait_for(lambda: engine.jev.asked)
    assert engine.jev.asked == [False]
    assert engine.suspicion.score("v1", PID) == 0.0
    await engine.stop()


@pytest.mark.asyncio
async def test_a_warning_asks_no_score_question(monkeypatch):
    async def no_act(*args, **kw):
        pass

    monkeypatch.setattr(engine_mod, "act", no_act)
    engine, minds, _ = await setup("v1")
    engine.jev = Rater()
    engine.facts.record(Utterance("u-0", "v2", "V2", "square", "Mind the newcomer", None, "warned",
                                  names_newcomer=True, action=WARN, about=PID))
    engine.suspect("v1", PID, 0.1, "was warned about the newcomer", "u-0")
    engine.hear_directly("v1", Match("utt:u-0", {"id": "u-0", "text": "V2 warned: Mind the newcomer"}))
    await wait_for(lambda: engine.jev.asked)
    assert engine.jev.asked == [False]
    assert engine.suspicion.score("v1", PID) == pytest.approx(0.1)
    await engine.stop()


def told(engine, uid, depth=0, audience="one", action=None):
    engine.facts.record(Utterance(uid, "v9", "V9", "tavern", "news", None, depth=depth, audience=audience, action=action))


@pytest.mark.asyncio
async def test_deep_retelling_does_not_wake(turns):
    engine, minds, _ = await setup("v1")
    told(engine, "u-deep", depth=3)
    minds["v1"].results = [[Match("k-deep", {"id": "u-deep", "text": "old news"}, 0.9, 0.004)]]
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 1)
    await asyncio.sleep(SETTLE)
    assert turns.views == []
    await engine.stop()


@pytest.mark.asyncio
async def test_room_talk_needs_the_overheard_margin(turns):
    engine, minds, _ = await setup("v1", cooldown=0.0)
    told(engine, "u-room", audience="room")
    told(engine, "u-loud", audience="room")
    minds["v1"].results = [
        [Match("k-room", {"id": "u-room", "text": "murmur"}, 0.9, 0.004)],
        [Match("k-loud", {"id": "u-loud", "text": "shout"}, 0.9, 0.015)],
    ]
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 1)
    await asyncio.sleep(SETTLE)
    assert turns.views == []
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["k-loud"]
    await engine.stop()


@pytest.mark.asyncio
async def test_stale_items_become_memories(turns):
    engine, minds, _ = await setup("v1")
    told(engine, "u-stale", depth=5)
    fresh = Match("k-fresh", {"id": "u-fresh", "text": "hot news"}, 0.9, 0.05)
    stale = Match("k-stale", {"id": "u-stale", "text": "old news"}, 0.9, 0.007)
    minds["v1"].results = [[fresh, stale]]
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    view = turns.views[0]
    assert view.new == [fresh]
    assert stale in view.recent
    assert turns.acts[0][1] == ["hot news"]
    await engine.stop()


@pytest.mark.asyncio
async def test_no_turn_without_a_fresh_item(turns):
    engine, minds, _ = await setup("v1", cooldown=0.0)
    told(engine, "u-stale", depth=5)
    stale = Match("k-stale", {"id": "u-stale", "text": "old news"}, 0.9, 0.007)
    fresh = Match("k-fresh", {"id": "u-fresh", "text": "hot news"}, 0.9, 0.05)
    minds["v1"].results = [[stale], [stale, fresh]]
    push(minds["v1"])
    await wait_for(lambda: minds["v1"].polls == 1)
    await asyncio.sleep(SETTLE)
    assert turns.views == []
    assert "v1" not in engine._last_turn
    assert "k-stale" in engine._consumed["v1"]
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    assert turns.views[0].new == [fresh]
    await engine.stop()


@pytest.mark.asyncio
async def test_the_newcomers_whisper_is_fresh_even_with_negative_clearance(turns):
    engine, minds, _ = await setup("v1")
    engine.lineage.record("w-1", None, player=PID)
    minds["v1"].results = [[Match("k-w", {"id": "w-1", "text": "psst"}, 0.4, -0.01)]]
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["k-w"]
    await engine.stop()


@pytest.mark.asyncio
async def test_warnings_always_fresh(turns):
    engine, minds, _ = await setup("v1")
    warning = Utterance("u-warn", "v2", "V2", "tavern", "watch the newcomer", None, depth=4, action=WARN)
    engine.facts.record(warning)
    minds["v1"].results = [[]]
    await engine.deliver(warning, ["v1"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["utt:u-warn"]
    await engine.stop()


async def stub_rumors(similarity):
    """Rumors whose check scores every line at similarity against a 0.5 baseline."""
    contex = StubContex()
    contex.default = [Match("rumor:w-1", {}, similarity)]
    rumors = Rumors(contex, StubBaselines(0.5), Config())
    await rumors.add("w-1", "psst", datetime.now(UTC), "v1", "V1", player=PID)
    return rumors


@pytest.mark.asyncio
async def test_off_topic_descendant_is_not_a_hop_and_marks_drift(turns):
    rumors = await stub_rumors(0.9)
    lines = []
    lineage = Lineage()
    town = Town()
    minds = {}

    def factory(v):
        minds[v.id] = FakeMind(v)
        return minds[v.id]

    feed = Feed(lineage, town, out=lines.append)
    engine = Engine(Config(), town, None, None, None, lineage, Ids(), feed, mind_factory=factory, rumors=rumors)
    for vid in ("v1", "v2", "v3"):
        await engine.add_villager(make(vid))
    lineage.record("w-1", None, player=PID)
    for uid, parent in (("u-1", "w-1"), ("u-2", "u-1"), ("u-3", "u-2")):
        lineage.record(uid, parent)
    await engine.deliver(Utterance("u-1", "v1", "V1", "square", "psst again", "w-1"), ["v2"])
    rumors.contex.default = [Match("rumor:w-1", {}, 0.4)]
    await engine.deliver(Utterance("u-2", "v2", "V2", "square", "the weather", "u-1"), ["v3"])
    await engine.deliver(Utterance("u-3", "v3", "V3", "square", "more weather", "u-2"), ["v1"])
    assert [(e.kind, e.data.get("uid") or e.data.get("whisper_id")) for e in feed.recent] == [
        ("hop", "u-1"), ("drift", "w-1"),
    ]
    assert lines == ['V1 told V2: "psst again"']
    assert [k for k, _ in minds["v3"].heard] == ["utt:u-2"] and [k for k, _ in minds["v1"].heard] == ["utt:u-3"]
    card = rumors.card("w-1")
    assert ([h["uid"] for h in card.hops], card.drifted) == (["u-1"], True)
    await engine.stop()


@pytest.mark.asyncio
async def test_took_the_bait_needs_an_on_topic_relay(relays):
    engine, minds, feed = await setup("v1", "v2", rumors=await stub_rumors(0.4))
    minds["v1"].results = [[]]
    relays.plan = [(SPEAK, ["v2"])]
    whisper(engine, "v1")
    await wait_for(lambda: statuses(feed))
    assert statuses(feed) == [("w-1", "v1", "kept", None)]
    await engine.stop()


@pytest.mark.asyncio
async def test_kept_after_an_off_topic_relay_upgrades_on_a_later_on_topic_relay(relays):
    rumors = await stub_rumors(0.4)
    engine, minds, feed = await setup("v1", "v2", rumors=rumors)
    minds["v1"].results = [[], [Match("utt:u-9", {"id": "u-9", "text": "the well"})]]
    engine.lineage.record("u-9", "w-1")
    relays.plan = [(SPEAK, ["v2"]), (SPEAK, ["v2"])]
    whisper(engine, "v1")
    await wait_for(lambda: statuses(feed))
    assert statuses(feed) == [("w-1", "v1", "kept", None)]
    rumors.contex.default = [Match("rumor:w-1", {}, 0.9)]
    push(minds["v1"])
    await wait_for(lambda: len(statuses(feed)) == 2)
    assert statuses(feed)[1] == ("w-1", "v1", "took_the_bait", 1)
    await engine.stop()


class RaisingBaselines:
    def matches(self, need, similarity):
        raise RuntimeError("no baseline")


async def overhearing_engine(overhear, *players):
    lineage, town = Lineage(), Town()
    feed = Feed(lineage, town, out=lambda line: None)
    engine = Engine(Config(), town, None, None, None, lineage, Ids(), feed, mind_factory=FakeMind, overhear=overhear)
    await engine.add_villager(make("v1"), "tavern")
    for player in players or [stub_player(place="tavern")]:
        engine.players.add(player)
    return engine, feed


@pytest.mark.asyncio
async def test_each_player_overhears_only_talk_at_their_own_place(turns):
    overhear = Overhear(StubContex(), StubBaselines(0.5), Config(overhear_rate=1.0), random.Random(1))
    engine, feed = await overhearing_engine(overhear, stub_player("p-a", "tavern"), stub_player("p-b", "well"))
    await engine.deliver(Utterance("u-1", "v2", "V2", "tavern", "the weather", None, action=SPEAK), ["v1"])
    assert [e.player for e in feed.recent if e.kind == "overheard"] == ["p-a"]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_listening_player_hears_every_line_at_their_place(turns):
    overhear = Overhear(StubContex(), StubBaselines(0.5), Config(overhear_rate=0.0), Always(0.99))
    engine, feed = await overhearing_engine(
        overhear, stub_player("p-1", "tavern", listening=True), stub_player("p-2", "tavern"),
    )
    await engine.deliver(Utterance("u-1", "v2", "V2", "tavern", "the weather", None, action=SPEAK), ["v1"])
    assert [(e.player, e.data["reason"]) for e in feed.recent if e.kind == "overheard"] == [("p-1", "listen")]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_rumor_hop_is_village_talk_to_another_player(turns):
    hops = []

    async def keep(utterance, place, hop, goals=(), listening=False):
        hops.append((hop, goals))
        return None

    overhear = Overhear(StubContex(), StubBaselines(0.5), Config(), random.Random(1))
    overhear.keep = keep
    pa = stub_player("p-a", "tavern")
    pa.goals = [SimpleNamespace(id="well-cursed")]
    engine, _ = await overhearing_engine(overhear, pa, stub_player("p-b", "tavern"))
    engine.lineage.record("w-1", None, player="p-a")
    engine.lineage.record("u-1", "w-1")
    await engine.deliver(Utterance("u-1", "v2", "V2", "tavern", "psst again", "w-1", action=SPEAK), ["v1"])
    assert sorted(hops) == [(False, []), (True, ["well-cursed"])]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failing_goal_check_still_lets_the_line_be_overheard(turns):
    contex = StubContex()
    contex.default = [Match("goal:g", {}, 0.9)]
    overhear = Overhear(contex, RaisingBaselines(), Config(overhear_rate=1.0), random.Random(1))
    overhear._evidence = {"goal:g": "the well"}
    player = stub_player(place="tavern")
    player.goals = [SimpleNamespace(id="g")]
    engine, feed = await overhearing_engine(overhear, player)
    await engine.deliver(Utterance("u-1", "v2", "V2", "tavern", "the weather", None, action=SPEAK), ["v1"])
    assert [(e.kind, e.data["reason"]) for e in feed.recent] == [("overheard", "random")]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failing_overhear_does_not_fail_the_delivery(turns):
    overhear = Overhear(StubContex(), StubBaselines(0.5), Config(), random.Random(1))

    async def broken(*args, **kwargs):
        raise RuntimeError("keep failed")

    overhear.keep = broken
    engine, feed = await overhearing_engine(overhear)
    await engine.deliver(Utterance("u-1", "v2", "V2", "tavern", "the weather", None, action=SPEAK), ["v1"])
    assert engine.minds["v1"].heard
    assert not feed.recent
    await engine.stop()


@pytest.mark.asyncio
async def test_overheard_lines_reach_the_feed_but_card_hops_do_not(turns):
    rumors = await stub_rumors(0.9)
    lineage, town, lines = Lineage(), Town(), []
    feed = Feed(lineage, town, out=lines.append)
    overhear = Overhear(StubContex(), StubBaselines(0.5), Config(overhear_rate=1.0), random.Random(1))
    engine = Engine(
        Config(), town, None, None, None, lineage, Ids(), feed, mind_factory=FakeMind, rumors=rumors, overhear=overhear,
    )
    for vid in ("v1", "v2"):
        await engine.add_villager(make(vid), "tavern")
    engine.players.add(stub_player(place="tavern"))
    lineage.record("w-1", None, player=PID)
    lineage.record("u-1", "w-1")
    lineage.record("u-2", None)
    await engine.deliver(Utterance("u-1", "v1", "V1", "tavern", "psst again", "w-1", action=SPEAK), ["v2"])
    await engine.deliver(Utterance("u-2", "v2", "V2", "tavern", "the weather", None, action=SPEAK), ["v1"])
    await engine.deliver(Utterance("u-3", "v2", "V2", "tavern", "V2 joined the Watch", None, "announced",
                                   action="join_group"), ["v1"])
    assert [(e.kind, e.data.get("uid")) for e in feed.recent] == [
        ("hop", "u-1"), ("overheard", "u-2"), ("announcement", None),
    ]
    assert feed.recent[1].data == {
        "speaker": "v2", "speaker_name": "V2", "place": "tavern", "text": "the weather", "reason": "random", "uid": "u-2",
        "scene": None,
    }
    assert lines == ['V1 told V2: "psst again"']
    await engine.stop()


def said_line(uid="u-9", text="Ada said: the well is fine"):
    return Match(f"said:{uid}", {"id": uid, "text": text}, 0.9)


@pytest.mark.asyncio
async def test_spoken_lines_go_to_the_said_project(turns):
    contex = StubContex()
    engine, _, _ = await setup("v1", "v2", contex=contex)
    utt = Utterance("u-1", "v1", "V1", "square", "the well is fine", None, "announced")
    await engine.deliver(utt, ["v2"])
    assert contex.published == [(said_project(engine.config.session, "v1"), "said:u-1", utt.to_data())]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_town_crier_line_is_not_remembered_as_said(turns):
    contex = StubContex()
    engine, _, _ = await setup("v1", contex=contex)
    await engine.deliver(Utterance("u-1", "town", "The town crier", "gate", "a newcomer arrives", None, "announced"), ["v1"])
    assert contex.published == []
    await engine.stop()


@pytest.mark.asyncio
async def test_the_players_whispers_are_not_remembered_as_said(turns):
    contex = StubContex()
    engine, _, _ = await setup("v1", contex=contex)
    utt = Utterance("w-1", NEWCOMER, "A newcomer", "square", "psst", None, "whispered to you")
    await engine.deliver(utt, ["v1"])
    assert contex.published == []
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failed_said_publish_is_logged_with_ids_only_and_delivery_goes_on(turns, caplog):
    contex = StubContex()

    async def broken(*args):
        raise RuntimeError("down 0.93")

    contex.publish = broken
    engine, minds, _ = await setup("v1", "v2", contex=contex)
    utt = Utterance("u-1", "v1", "V1", "square", "the well is fine", None)
    with caplog.at_level(logging.WARNING):
        await engine.deliver(utt, ["v2"])
    assert minds["v2"].heard
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "v1" in warning.getMessage() and "0.93" not in warning.getMessage() and "well" not in warning.getMessage()
    await engine.stop()


async def whisper_turn(turns, contex, **config):
    engine, minds, _ = await setup("v1", contex=contex, config=Config(**{"cooldown_seconds": 0.05, **config}))
    minds["v1"].results = [[]]
    engine.lineage.record("w-1", None, player=PID)
    text = "A newcomer whispered to you: psst"
    engine.hear_directly("v1", Match("utt:w-1", {"id": "w-1", "text": text}, 0.8))
    await wait_for(lambda: len(turns.views) == 1)
    await engine.stop()
    return engine, turns.views[0]


@pytest.mark.asyncio
async def test_newcomer_line_retrieves_related_said_lines(turns):
    contex = StubContex()
    contex.default = [said_line("u-1"), Match("said:u-2", {"id": "u-2", "text": "unrelated"}, 0.5)]
    engine, view = await whisper_turn(turns, contex)
    assert [m.data_key for m in view.said] == ["said:u-1"]
    [(project, query, kwargs)] = [q for q in contex.queries if q[0] == said_project(engine.config.session, "v1")]
    assert query == "psst"
    assert kwargs == {"top_k": 3, "since": None, "threshold": 0}


@pytest.mark.asyncio
async def test_a_turn_without_a_newcomer_line_does_not_search_said(turns):
    contex = StubContex()
    engine, minds, _ = await setup("v1", contex=contex)
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    assert contex.queries == []
    assert turns.views[0].said == []
    await engine.stop()


@pytest.mark.asyncio
async def test_said_floor_is_configurable(turns):
    contex = StubContex()
    contex.default = [said_line()]
    _, view = await whisper_turn(turns, contex, said_floor=0.95)
    assert view.said == []


@pytest.mark.asyncio
async def test_a_failed_said_search_leaves_no_lines_and_logs_ids_only(turns, caplog):
    contex = StubContex(error=RuntimeError("down 0.93"))
    with caplog.at_level(logging.WARNING):
        _, view = await whisper_turn(turns, contex)
    assert view.said == []
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "v1" in warning.getMessage() and "0.93" not in warning.getMessage()


@pytest.mark.asyncio
async def test_said_project_has_no_subscription(turns):
    contex = StubContex()
    contex.default = [said_line()]
    subscribed = []

    async def subscribe(project_id, *args, **kwargs):
        subscribed.append(project_id)

    contex.subscribe = subscribe
    engine, _ = await whisper_turn(turns, contex)
    utt = Utterance("u-1", "v1", "V1", "square", "the well is fine", None)
    await engine.deliver(utt, [])
    assert contex.queries and contex.published
    assert not any(p == said_project(engine.config.session, "v1") for p in subscribed)


class Talks:
    """Each turn takes the next planned action, answering the newcomer's line; a planned None fails the turn."""

    def __init__(self):
        self.plan = []
        self.views = []
        self.moods = []

    async def decide(self, jev, view):
        self.views.append(view)
        action = self.plan.pop(0)
        if action is None:
            raise RuntimeError("jev down")
        return Decision(action, view.answerable[-1] if action == ANSWER else view.new[0], None, None)

    async def act(self, decision, villager, **kw):
        self.moods.append(kw.get("mood"))


@pytest.fixture
def talks(monkeypatch):
    t = Talks()
    monkeypatch.setattr(engine_mod, "decide", t.decide)
    monkeypatch.setattr(engine_mod, "act", t.act)
    return t


def reply_whisper(engine, vid, wid, conversation):
    engine.facts.record(Utterance(wid, NEWCOMER, "A newcomer", "square", "psst", None, conversation=conversation))
    whisper(engine, vid, wid)


def states(feed):
    return [(wid, state) for wid, _, state, _ in statuses(feed)]


async def answered_once(talks, then, **config):
    engine, minds, feed = await setup("v1", config=Config(**{"cooldown_seconds": 0.05, **config}))
    minds["v1"].results = [[], []]
    talks.plan = [ANSWER, then]
    whisper(engine, "v1", "w-1")
    await wait_for(lambda: statuses(feed))
    assert [m.data_key for m in talks.views[0].answerable] == ["utt:w-1"]
    assert engine.conversations[("v1", PID)] == {"id": "w-1", "answered": ["w-1"]}
    reply_whisper(engine, "v1", "w-2", "w-1")
    await wait_for(lambda: len(statuses(feed)) >= 3)
    await engine.stop()
    return engine, feed


@pytest.mark.asyncio
async def test_cap_ends_with_moved_on(talks):
    engine, feed = await answered_once(talks, ANSWER, talk_turns=2)
    assert states(feed) == [("w-1", "answered"), ("w-2", "answered"), ("w-1", "moved_on"), ("w-2", "moved_on")]
    assert ("v1", PID) not in engine.conversations


@pytest.mark.asyncio
async def test_other_action_ends_with_moved_on(talks):
    engine, feed = await answered_once(talks, IGNORE)
    assert states(feed) == [("w-1", "answered"), ("w-2", "moved_on"), ("w-1", "moved_on")]
    assert ("v1", PID) not in engine.conversations


@pytest.mark.asyncio
async def test_failed_turn_ends_with_moved_on(talks):
    engine, feed = await answered_once(talks, None)
    assert states(feed) == [("w-1", "answered"), ("w-2", "moved_on"), ("w-1", "moved_on")]
    assert ("v1", PID) not in engine.conversations


@pytest.mark.asyncio
async def test_one_villager_holds_a_conversation_with_each_player():
    engine, _, _ = await setup("v1")
    engine.conversations[("v1", "p-a")] = {"id": "w-1", "answered": ["w-1"]}
    engine.conversations[("v1", "p-b")] = {"id": "w-2", "answered": ["w-2"]}
    engine.end_conversation("v1", "p-b")
    assert list(engine.conversations) == [("v1", "p-a")]
    engine.end_conversation("v1")
    assert engine.conversations == {}
    await engine.stop()


@pytest.mark.asyncio
async def test_answerable_leaves_out_a_player_whose_conversation_is_full(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[]]
    full = ["w-0"] * engine.config.talk_turns
    engine.conversations[("v1", "p-a")] = {"id": "w-0", "answered": full}
    engine.lineage.record("w-1", None, player="p-a")
    engine.lineage.record("w-2", None, player="p-b")
    engine.hear_directly("v1", Match("utt:w-1", {"id": "w-1", "text": "psst"}, 0.8))
    engine.hear_directly("v1", Match("utt:w-2", {"id": "w-2", "text": "psst"}, 0.8))
    await wait_for(lambda: len(turns.views) == 1)
    await engine.stop()
    assert [m.data["id"] for m in turns.views[0].answerable] == ["w-2"]


@pytest.mark.asyncio
async def test_no_answer_is_offered_without_a_newcomer_line(talks):
    engine, minds, _ = await setup("v1")
    talks.plan = [IGNORE]
    push(minds["v1"])
    await wait_for(lambda: talks.views)
    assert talks.views[0].answerable == []
    await engine.stop()


@pytest.mark.asyncio
async def test_departure_ends_the_conversation(talks):
    engine, _, feed = await setup("v1")
    engine.conversations[("v1", PID)] = {"id": "w-1", "answered": ["w-1"]}
    await engine.remove_villager("v1")
    assert states(feed) == [("w-1", "moved_on")]
    assert ("v1", PID) not in engine.conversations
    await engine.stop()


@pytest.mark.asyncio
async def test_a_reply_is_remembered_and_reaches_the_feed_not_minds(turns):
    contex = StubContex()
    engine, minds, _ = await setup("v1", "v2", contex=contex)
    lines = []
    engine.feed = Feed(engine.lineage, engine.town, out=lines.append)
    engine.facts.record(Utterance("w-2", NEWCOMER, "A newcomer", "square", "psst", None, conversation="w-1"))
    utt = Utterance("u-5", "v1", "V1", "square", "Aye, the well.", "w-2", "answered", action=ANSWER)
    await engine.reply(utt)
    assert minds["v2"].heard == [] and minds["v1"].heard == []
    assert contex.published == [(said_project(engine.config.session, "v1"), "said:u-5", utt.to_data())]
    event = engine.feed.recent[-1]
    assert event.kind == "reply"
    assert {k: event.data[k] for k in ("conversation", "villager_id", "name", "text", "uid")} == {
        "conversation": "w-1", "villager_id": "v1", "name": "V1", "text": "Aye, the well.", "uid": "u-5",
    }
    assert lines == ['V1 answers you: "Aye, the well."']
    await engine.stop()


@pytest.mark.asyncio
async def test_two_replies_in_one_turn_keep_the_conversation_when_one_is_answered(talks):
    engine, minds, feed = await setup("v1")
    minds["v1"].results = [[]]
    talks.plan = [ANSWER]
    engine.conversations[("v1", PID)] = {"id": "w-1", "answered": ["w-1"]}
    reply_whisper(engine, "v1", "w-2", "w-1")
    reply_whisper(engine, "v1", "w-3", "w-1")
    await wait_for(lambda: len(statuses(feed)) == 2)
    await engine.stop()
    assert states(feed) == [("w-2", "moved_on"), ("w-3", "answered")]
    assert engine.conversations[("v1", PID)] == {"id": "w-1", "answered": ["w-1", "w-3"]}


@pytest.mark.asyncio
async def test_an_answer_after_the_conversation_closed_reopens_it_on_its_card(talks):
    engine, minds, feed = await setup("v1")
    minds["v1"].results = [[]]
    talks.plan = [ANSWER]
    reply_whisper(engine, "v1", "w-3", "w-1")
    await wait_for(lambda: statuses(feed))
    await engine.stop()
    assert engine.conversations[("v1", PID)] == {"id": "w-1", "answered": ["w-3"]}


@pytest.mark.asyncio
async def test_share_chance_is_nature_times_how_close_the_news_came():
    engine, _, _ = await setup("v1")
    engine._gossip["v1"] = 0.8
    news = lambda clearance: Match("k", {"id": "u"}, 0.9, clearance)  # noqa: E731
    assert engine.share_chance("v1", news(0.01)) == pytest.approx(0.4)
    assert engine.share_chance("v1", news(0.05)) == pytest.approx(0.8)
    assert engine.share_chance("v1", news(None)) == pytest.approx(0.4)
    assert engine.share_chance("v2", news(-0.01)) == 0.0
    await engine.stop()


class Talkers:
    """Each villager's turn takes their next planned (action, keen), ignoring once the plan runs out; a planned None
    fails the turn. A speech turn says "more" at the speaker's place to whoever said the source."""

    def __init__(self):
        self.plan: dict[str, list] = {}
        self.views = []
        self.engine = None
        # The scene participants each turn saw when it decided.
        self.within = []

    async def decide(self, jev, view):
        self.views.append(view)
        scene = self.engine.scenes.of(view.villager.id)
        self.within.append(set(scene.participants) if scene else None)
        planned = self.plan.get(view.villager.id) or [(IGNORE, None)]
        action, keen = planned.pop(0) or (None, None)
        if action is None:
            raise RuntimeError("jev down")
        source = view.answerable[-1] if action == ANSWER else view.new[0]
        return Decision(action, source, None, None, keen=keen)

    async def act(self, decision, villager, *, town, hub, lineage, ids, facts, **kw):
        if decision.action not in SCENE_SPEECH:
            return
        source_id = decision.source.data["id"]
        uid = ids.next("u")
        lineage.record(uid, source_id)
        line = Utterance(uid, villager.id, villager.name, town.place_of(villager.id), "more", source_id,
                         action=decision.action)
        facts.record(line)
        await hub.deliver(line, [facts.get(source_id).speaker])


@pytest.fixture
def talkers(monkeypatch):
    t = Talkers()
    monkeypatch.setattr(engine_mod, "decide", t.decide)
    monkeypatch.setattr(engine_mod, "act", t.act)
    return t


def said_by(engine, uid, speaker, place="square", action=SPEAK, style=None):
    engine.facts.record(Utterance(uid, speaker, speaker.upper(), place, "news", None, action=action, style=style))


def hand(engine, vid, uid):
    """Put the line uid in vid's next turn."""
    engine.hear_directly(vid, Match(f"utt:{uid}", {"id": uid, "text": "news"}))


async def talk(talkers, *vids, similarity=0.9, **config):
    """An engine whose minds poll nothing, whose topics all score similarity, and that draws no bystanders."""
    rumors = await stub_rumors(similarity)
    rumors.contex.default = [Match(f"rumor:{uid}", {}, similarity) for uid in ("w-1", "o-1", "o-2")]
    engine, minds, feed = await setup(*vids, rumors=rumors, config=Config(**{"cooldown_seconds": 0.05, **config}))
    for mind in minds.values():
        mind.results = [[]] * 50
    engine.scenes.rng = Always(1.0)
    talkers.engine = engine
    return engine, minds, feed


def scene_events(feed):
    return [e.data for e in feed.recent if e.kind == "scene"]


@pytest.mark.asyncio
async def test_an_answered_line_at_the_players_place_starts_a_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: len(talkers.views) == 2)
    assert talkers.within[1] == {"v1", "v2"}
    assert talkers.views[1].villager.id == "v1"
    assert [m.data["id"] for m in talkers.views[1].new] == ["u-1"]
    started = scene_events(feed)
    assert started[0]["people"] == 2
    assert (started[0]["scene_id"], started[0]["rumor"], started[0]["opening"]) == ("s-1", None, "o-1")
    assert (started[0]["place"], started[0]["tone"], started[0]["ended"]) == ("square", "conversation", None)
    await engine.stop()


@pytest.mark.asyncio
async def test_village_talk_away_from_the_player_starts_no_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    engine.players.get(PID).place = "tavern"
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: feed.deliveries)
    await asyncio.sleep(SETTLE)
    assert scene_events(feed) == [] and engine.scenes.of("v2") is None
    assert len(talkers.views) == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_an_on_card_hop_starts_a_scene_anywhere_and_an_off_card_one_does_not(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    engine.players.get(PID).place = "tavern"
    engine.lineage.record("w-1", None, player=PID)
    talkers.plan = {"v2": [(SPEAK, 1.0), (SPEAK, 1.0)]}
    engine.lineage.record("o-2", "w-1")
    said_by(engine, "o-2", "v1")
    hand(engine, "v2", "o-2")
    await wait_for(lambda: feed.deliveries)
    assert scene_events(feed) == []
    engine.lineage.record("o-1", "w-1")
    said_by(engine, "o-1", "v1")
    engine.rumors.record("w-1", engine.facts.get("o-1"), {"v2": "V2"}, True)
    hand(engine, "v2", "o-1")
    await wait_for(lambda: scene_events(feed))
    assert (scene_events(feed)[0]["rumor"], scene_events(feed)[0]["opening"]) == ("w-1", "o-1")
    await engine.stop()


@pytest.mark.asyncio
async def test_the_newcomers_whisper_never_opens_a_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    engine.lineage.record("w-1", None, player=PID)
    said_by(engine, "w-1", NEWCOMER)
    hand(engine, "v2", "w-1")
    await wait_for(lambda: feed.deliveries)
    assert scene_events(feed) == []
    assert talkers.views[0].ask_keen is False
    await engine.stop()


@pytest.mark.asyncio
async def test_scene_lines_are_addressed_and_wait_only_the_scene_cooldown(talkers):
    engine, _, _ = await talk(talkers, "v1", "v2", cooldown_seconds=5, scene_cooldown_seconds=0.05)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: len(talkers.views) == 2)
    assert talkers.views[1].villager.id == "v1"
    assert [m.data_key for m in talkers.views[1].new] == ["utt:u-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_max_scenes_holds_back_a_new_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", "v3", "v4", max_scenes=1, scene_cooldown_seconds=5)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v4": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: len(feed.deliveries) == 1)
    said_by(engine, "o-2", "v3")
    hand(engine, "v4", "o-2")
    await wait_for(lambda: len(feed.deliveries) == 2)
    assert {e["scene_id"] for e in scene_events(feed)} == {"s-1"}
    assert engine.scenes.of("v4") is None
    assert [k for _, k, _ in engine.rumors.contex.published] == ["rumor:w-1", "rumor:o-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_two_answers_to_one_exchange_open_one_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", "v3", scene_cooldown_seconds=5)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v3": [(SPEAK, 1.0)]}
    add_topic = engine.rumors.add_topic

    async def slow_add_topic(*args):
        await asyncio.sleep(SETTLE)
        await add_topic(*args)

    engine.rumors.add_topic = slow_add_topic
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    hand(engine, "v3", "o-1")
    await wait_for(lambda: len(feed.deliveries) == 2)
    assert engine.scenes.of("v2") is engine.scenes.of("v3")
    assert {e["scene_id"] for e in scene_events(feed)} <= {"s-1"}
    await engine.stop()


@pytest.mark.asyncio
async def test_an_off_topic_line_ends_the_scene_and_is_not_addressed(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", similarity=0.4)
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    engine.scenes.start(engine.facts.get("o-1"), "v2", "o-1", None).people.add("v2")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: feed.deliveries)
    await asyncio.sleep(SETTLE)
    assert scene_events(feed)[-1]["ended"] == "The conversation turned to other things."
    assert "v1" not in engine._pending
    assert len(talkers.views) == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_a_non_speech_turn_breaks_up_a_two_person_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v1": [(IGNORE, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: scene_events(feed) and scene_events(feed)[-1]["ended"])
    assert scene_events(feed)[-1]["ended"] == "The conversation broke up."
    assert engine.scenes.of("v2") is None
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failed_turn_leaves_the_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v1": [None]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: scene_events(feed) and scene_events(feed)[-1]["ended"])
    assert scene_events(feed)[-1]["ended"] == "The conversation broke up."
    assert engine.scenes.of("v1") is None
    await engine.stop()


@pytest.mark.asyncio
async def test_a_participant_leaving_town_ends_the_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", scene_cooldown_seconds=5)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: feed.deliveries)
    assert scene_events(feed)[-1]["ended"] is None
    await engine.remove_villager("v1")
    assert scene_events(feed)[-1]["ended"] == "The conversation broke up."
    await engine.stop()


@pytest.mark.asyncio
async def test_answering_the_newcomer_leaves_the_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", scene_cooldown_seconds=1)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v1": [(ANSWER, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: engine._pending.get("v1"))
    whisper(engine, "v1")
    await wait_for(lambda: len(talkers.views) == 2)
    await wait_for(lambda: scene_events(feed)[-1]["ended"])
    assert [m.data_key for m in talkers.views[1].new] == ["utt:u-1", "utt:w-1"]
    assert scene_events(feed)[-1]["ended"] == "The conversation broke up."
    assert engine.conversations[("v1", PID)]["answered"] == ["w-1"]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_scene_line_that_also_matches_a_need_still_reaches_the_turn(talkers):
    engine, minds, feed = await talk(talkers, "v1", "v2")
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v1": [(SPEAK, 1.0)]}
    minds["v1"].results = [[Match("utt:u-1", {"id": "u-1", "text": "more"}, 0.9, -0.01)]] + [[]] * 50
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: len(talkers.views) == 2)
    assert [(m.data_key, m.clearance) for m in talkers.views[1].new] == [("utt:u-1", None)]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_stalled_scene_cools_and_frees_its_slot(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", "v3", "v4", max_scenes=1, scene_cooldown_seconds=0.05)
    said_by(engine, "o-1", "v1")
    stalled = engine.scenes.start(engine.facts.get("o-1"), "v2", "o-1", None)
    stalled.people.add("v2")
    stalled.last_line -= 1.0
    talkers.plan = {"v4": [(SPEAK, 1.0)]}
    said_by(engine, "o-2", "v3")
    hand(engine, "v4", "o-2")
    await wait_for(lambda: feed.deliveries)
    assert stalled.ended == "cooled"
    assert scene_events(feed)[0]["ended"] == "The conversation died down."
    assert scene_events(feed)[1]["scene_id"] == "s-2"
    await engine.stop()


@pytest.mark.asyncio
async def test_keen_is_asked_only_when_a_scene_could_follow(talkers):
    engine, _, _ = await talk(talkers, "v1", "v2")
    talkers.plan = {"v2": [(IGNORE, None), (SPEAK, 1.0)], "v1": [(SPEAK, 1.0)]}
    hand(engine, "v2", "x-1")
    await wait_for(lambda: len(talkers.views) == 1)
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: len(talkers.views) == 3)
    assert [(v.villager.id, v.ask_keen) for v in talkers.views] == [("v2", False), ("v2", True), ("v1", True)]
    await engine.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("stays_on_topic", [True, False])
async def test_a_scene_that_ends_while_a_line_is_checked_is_not_ended_again_or_addressed(talkers, stays_on_topic):
    engine, _, feed = await talk(talkers, "v1", "v2")
    said_by(engine, "o-1", "v1")
    scene = engine.scenes.start(engine.facts.get("o-1"), "v2", "o-1", None)
    scene.people.add("v2")
    checking, release = asyncio.Event(), asyncio.Event()

    async def slow_on_topic(topic, text):
        checking.set()
        await release.wait()
        return stays_on_topic

    engine.rumors.on_topic = slow_on_topic
    line = Utterance("u-1", "v2", "V2", "square", "more", "o-1", action=SPEAK)
    delivery = asyncio.create_task(engine.deliver(line, ["v1"]))
    await checking.wait()
    engine._leave_scene("v1")
    release.set()
    await delivery
    assert [e["ended"] for e in scene_events(feed)] == ["The conversation broke up."]
    assert scene.lines == ["o-1"]
    assert "v1" not in engine._pending
    await engine.stop()


def scene_players(feed):
    return [e.player for e in feed.recent if e.kind == "scene"]


@pytest.mark.asyncio
async def test_a_village_talk_scene_shows_only_to_players_at_its_place(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    engine.players = Players(feed)
    engine.players.add(stub_player("p-a"))
    engine.players.add(stub_player("p-b", "well"))
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: scene_events(feed))
    assert scene_players(feed) and all(p == "p-a" for p in scene_players(feed))
    await engine.stop()


@pytest.mark.asyncio
async def test_a_scene_end_reaches_a_player_who_walked_away(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", scene_cooldown_seconds=5)
    engine._last_turn["v1"] = time.monotonic()
    engine.players = Players(feed)
    walker = stub_player("p-a")
    engine.players.add(walker)
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: scene_events(feed))
    walker.place = "well"
    engine._end_scene(engine.scenes.of("v2"), "cooled")
    ended = [e.player for e in feed.recent if e.kind == "scene" and e.data["ended"]]
    assert ended == ["p-a"]
    await engine.stop()


def overheard(feed):
    return [(e.data["uid"], e.data["reason"], e.data["scene"]) for e in feed.recent if e.kind == "overheard"]


@pytest.mark.asyncio
async def test_a_drawn_bystander_who_answers_joins_the_scene(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2", "v3", scene_cooldown_seconds=5)
    engine.scenes.rng = Always(0.0)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v3": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: any(e["people"] == 3 for e in scene_events(feed)))
    assert talkers.views[1].villager.id == "v3"
    assert {e["scene_id"] for e in scene_events(feed)} == {"s-1"}
    assert engine.scenes.of("v3").id == "s-1"
    await engine.stop()


@pytest.mark.asyncio
async def test_a_bystander_hearing_through_their_needs_joins_the_same_way(talkers):
    engine, minds, feed = await talk(talkers, "v1", "v2", "v3", scene_cooldown_seconds=5)
    engine._last_turn["v1"] = time.monotonic()
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v3": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: feed.deliveries)
    assert "v3" not in engine._pending
    minds["v3"].results = [[Match("utt:u-1", {"id": "u-1", "text": "more"})]] + [[]] * 50
    push(minds["v3"])
    await wait_for(lambda: any(e["people"] == 3 for e in scene_events(feed)))
    assert {e["scene_id"] for e in scene_events(feed)} == {"s-1"}
    await engine.stop()


@pytest.mark.asyncio
async def test_a_full_scene_turns_a_seventh_speaker_away(talkers):
    vids = [f"v{i}" for i in range(1, 8)]
    engine, _, feed = await talk(talkers, *vids)
    said_by(engine, "o-1", "v1")
    scene = engine.scenes.start(engine.facts.get("o-1"), "v2", "o-1", None)
    for vid in vids[2:6]:
        assert engine.scenes.join(scene, vid)
    talkers.plan = {"v7": [(SPEAK, 1.0)]}
    hand(engine, "v7", "o-1")
    await wait_for(lambda: feed.deliveries)
    await asyncio.sleep(SETTLE)
    assert engine.scenes.of("v7") is None
    assert len(scene.participants) == 6
    assert scene_events(feed) == []
    await engine.stop()


@pytest.mark.asyncio
async def test_every_line_of_a_village_scene_is_overheard(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    engine.overhear = Overhear(StubContex(), StubBaselines(0.5), Config(overhear_rate=0.0), random.Random(1))
    talkers.plan = {"v2": [(SPEAK, 1.0)], "v1": [(SPEAK, 1.0)]}
    said_by(engine, "o-1", "v1")
    hand(engine, "v2", "o-1")
    await wait_for(lambda: len(feed.deliveries) == 2)
    await asyncio.sleep(SETTLE)
    assert overheard(feed) == [("u-1", "scene", "s-1"), ("u-2", "scene", "s-1")]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_player_rooted_scene_line_is_a_hop_not_overheard(talkers):
    engine, _, feed = await talk(talkers, "v1", "v2")
    engine.overhear = Overhear(StubContex(), StubBaselines(0.5), Config(overhear_rate=1.0), random.Random(1))
    engine.lineage.record("w-1", None, player=PID)
    engine.lineage.record("o-1", "w-1")
    said_by(engine, "o-1", "v1")
    engine.rumors.record("w-1", engine.facts.get("o-1"), {"v2": "V2"}, True)
    talkers.plan = {"v2": [(SPEAK, 1.0)]}
    hand(engine, "v2", "o-1")
    await wait_for(lambda: feed.deliveries)
    await asyncio.sleep(SETTLE)
    assert scene_events(feed)[0]["rumor"] == "w-1"
    assert overheard(feed) == []
    await engine.stop()


def heard_line(uid, similarity, text="Bo said: the miller waters his flour"):
    return Match(f"utt:{uid}", {"id": uid, "text": text}, similarity)


@pytest.mark.asyncio
async def test_recall_heard_skips_the_whisper_itself_and_weak_matches():
    contex = StubContex()
    contex.default = [heard_line("w-1", 0.99), heard_line("u-3", 0.85), heard_line("u-2", 0.9), heard_line("u-4", 0.82)]
    engine, _, _ = await setup("v1", contex=contex)
    found = await engine.recall_heard("v1", "A newcomer whispered to you: psst", "w-1")
    assert [m.data_key for m in found] == ["utt:u-2", "utt:u-3"]
    [(project, query, kwargs)] = contex.queries
    assert (project, query) == (mind_project(engine.config.session, "v1"), "psst")
    assert kwargs == {"top_k": 3, "since": None, "threshold": 0}
    await engine.stop()


@pytest.mark.asyncio
async def test_recall_heard_looks_back_over_the_remembered_moves():
    contex = StubContex()
    game = Game(memory_moves=2)
    game.moved(PID)
    game.moved(PID)
    engine, _, _ = await setup("v1", contex=contex, game=game)
    await engine.recall_heard("v1", "psst", "w-1")
    assert contex.queries[0][2]["since"] == game.memory_since() is not None
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failed_heard_search_returns_nothing_and_logs_ids_only(caplog):
    engine, _, _ = await setup("v1", contex=StubContex(error=RuntimeError("down 0.93")))
    with caplog.at_level(logging.WARNING):
        assert await engine.recall_heard("v1", "psst", "w-1") == []
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "v1" in warning.getMessage() and "0.93" not in warning.getMessage()
    await engine.stop()


@pytest.mark.asyncio
async def test_the_turn_view_and_state_carry_what_they_heard(turns):
    contex = StubContex()
    contex.default = [heard_line("w-1", 0.99), heard_line("u-2", 0.9)]
    engine, view = await whisper_turn(turns, contex)
    assert [m.data_key for m in view.heard] == ["utt:u-2"]
    assert _state(view)["things_i_heard_before"] == ["Bo said: the miller waters his flour"]


@pytest.mark.asyncio
async def test_a_turn_without_a_newcomer_line_recalls_nothing_heard(turns):
    engine, minds, _ = await setup("v1")
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    assert turns.views[0].heard == []
    assert "things_i_heard_before" not in _state(turns.views[0])
    await engine.stop()


@pytest.mark.asyncio
async def test_recall_heard_leaves_out_the_newcomers_earlier_whispers():
    contex = StubContex()
    contex.default = [heard_line("w-1", 0.99), heard_line("u-2", 0.9)]
    engine, _, _ = await setup("v1", contex=contex)
    engine.lineage.record("w-1", None, player=PID)
    found = await engine.recall_heard("v1", "psst", "w-2")
    assert [m.data_key for m in found] == ["utt:u-2"]
    await engine.stop()


async def skip_act(*args, **kw):
    pass


def player_whisper(engine, wid, player):
    engine.lineage.record(wid, None, player=player)
    return Match(f"utt:{wid}", {"id": wid, "text": f"A newcomer whispered to you: {wid}"})


@pytest.mark.asyncio
async def test_a_named_line_raises_suspicion_of_the_player_it_roots_in(monkeypatch):
    monkeypatch.setattr(engine_mod, "act", skip_act)
    engine, minds, _ = await setup("v1")
    engine.jev = Rater()
    minds["v1"].results = [[player_whisper(engine, "w-1", "p-a")]]
    push(minds["v1"])
    await wait_for(lambda: engine.jev.asked)
    await wait_for(lambda: engine.suspicion.score("v1", "p-a") > 0)
    assert engine.suspicion.score("v1", "p-b") == 0.0
    await engine.stop()


@pytest.mark.asyncio
async def test_one_turn_naming_two_players_raises_each(monkeypatch):
    monkeypatch.setattr(engine_mod, "act", skip_act)
    engine, minds, _ = await setup("v1")
    engine.jev = Rater()
    minds["v1"].results = [[player_whisper(engine, "w-1", "p-a"), player_whisper(engine, "w-2", "p-b")]]
    push(minds["v1"])
    await wait_for(lambda: engine.suspicion.score("v1", "p-b") > 0)
    hear_max = engine.config.suspicion_hear_max
    assert engine.suspicion.score("v1", "p-a") == engine.suspicion.score("v1", "p-b") == hear_max
    await engine.stop()


@pytest.mark.asyncio
async def test_the_turn_weighs_suspicion_of_the_player_in_front(turns):
    engine, minds, _ = await setup("v1")
    engine.suspect("v1", "p-b", 0.6, "r")
    engine.suspect("v1", "p-a", 0.1, "r")
    minds["v1"].results = [[player_whisper(engine, "w-1", "p-a")]]
    push(minds["v1"])
    await wait_for(lambda: turns.views)
    assert turns.views[0].suspicion == pytest.approx(0.1)
    await engine.stop()


class TokenJev:
    """A Jev whose answers cost a million input tokens per id in the state's villager field."""

    def __init__(self, gate=None):
        self.gate = gate
        self.started = 0

    async def system_one(self, state, questions):
        self.started += 1
        if self.gate:
            await self.gate.wait()
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=1_000_000 * state["n"], output_tokens=0))


class Paying:
    """Each turn asks Jev, then has the writer speak about its first new item, as a real turn does."""

    async def decide(self, jev, view):
        await jev.system_one(state={"n": int(view.villager.id[1:])}, questions={})
        return Decision(SPEAK, view.new[0], None, None)

    async def act(self, decision, villager, *, writer, **kw):
        await writer.complete("system", "user")


async def paying_engine(monkeypatch, *vids, gate=None):
    p = Paying()
    monkeypatch.setattr(engine_mod, "decide", p.decide)
    monkeypatch.setattr(engine_mod, "act", p.act)
    engine, minds, feed = await setup(*vids)
    usage = Usage(Config())
    engine.jev = CountingJev(TokenJev(gate), usage)
    engine.writer = Writer(StubClient(*["Did you hear?"] * len(vids)), "haiku", usage)
    return engine, minds, usage


def hop(engine, uid, root, player):
    engine.lineage.record(root, None, player=player)
    engine.lineage.record(uid, root)
    return Match(f"utt:{uid}", {"id": uid, "text": "V9 said: the well is cursed"})


@pytest.mark.asyncio
async def test_a_turn_on_a_players_rumor_is_charged_to_that_player(monkeypatch):
    engine, minds, usage = await paying_engine(monkeypatch, "v1")
    minds["v1"].results = [[hop(engine, "u-1", "w-1", "p-a")]]
    push(minds["v1"])
    await wait_for(lambda: usage.tokens["haiku"][0])
    assert usage.tokens == {"haiku": [120, 30], "jev": [1_000_000, 0]}
    assert usage.cost("p-a") == pytest.approx(usage.cost()) and usage.cost() > 0
    await engine.stop()


@pytest.mark.asyncio
async def test_village_talk_is_charged_to_no_player(monkeypatch):
    engine, minds, usage = await paying_engine(monkeypatch, "v1")
    engine.lineage.record("u-0", None)
    minds["v1"].results = [[Match("utt:u-0", {"id": "u-0", "text": "V9 said: rain tomorrow"})]]
    push(minds["v1"])
    await wait_for(lambda: usage.tokens["haiku"][0])
    assert usage.cost() > 0 and usage.cost(PID) == usage.cost("p-a") == 0.0
    await engine.stop()


@pytest.mark.asyncio
async def test_concurrent_turns_charge_each_their_own_player(monkeypatch):
    gate = asyncio.Event()
    engine, minds, usage = await paying_engine(monkeypatch, "v1", "v2", gate=gate)
    minds["v1"].results = [[hop(engine, "u-1", "w-1", "p-a")]]
    minds["v2"].results = [[hop(engine, "u-2", "w-2", "p-b")]]
    push(minds["v1"])
    push(minds["v2"])
    await wait_for(lambda: engine.jev.client.started == 2)
    gate.set()
    await wait_for(lambda: usage.tokens["haiku"][0] == 240)
    assert usage.cost("p-a") + usage.cost("p-b") == pytest.approx(usage.cost())
    assert usage.cost("p-b") - usage.cost("p-a") == pytest.approx(0.04)
    await engine.stop()


@pytest.mark.asyncio
async def test_no_turn_while_nobody_is_playing_and_turns_resume_when_a_player_joins(turns):
    engine, minds, feed = await setup("v1")
    engine.players = Players(feed)
    push(minds["v1"])
    await asyncio.sleep(SETTLE)
    assert minds["v1"].polls == 0 and turns.views == []
    engine.players.add(stub_player())
    push(minds["v1"])
    await wait_for(lambda: len(turns.views) == 1)
    await engine.stop()


class HangingContex(StubContex):
    async def query(self, project_id, query, **kwargs):
        self.queries.append((project_id, query, kwargs))
        await asyncio.Event().wait()


async def following(turns, contex=None, baselines=True, listeners=("v2",), depth=0, **config):
    """An engine with a card for w-1 that delivers hop u-1 from v1 to listeners; v2 needs bread and ale."""
    rumors = await stub_rumors(0.9)
    engine, minds, feed = await setup(
        "v1", "v2", "v3", rumors=rumors, contex=contex, config=Config(**{"cooldown_seconds": 0.05, **config}),
    )
    if baselines:
        engine.baselines = StubBaselines(0.5)
    engine.town.get("v2").needs = ["bread", "ale"]
    engine.lineage.record("w-1", None, player=PID)
    engine.lineage.record("u-1", "w-1")
    await engine.deliver(Utterance("u-1", "v1", "V1", "square", "psst again", "w-1", depth=depth), list(listeners))
    return engine, minds, rumors


def entry(rumors, vid):
    return rumors._cards["w-1"].frontier.get(vid)


def mind_queries(engine, vid):
    return [q for q in engine.contex.queries if q[0] == mind_project(engine.config.session, vid)]


@pytest.mark.asyncio
async def test_a_listener_who_does_not_care_is_marked_with_queries_only(turns):
    engine, _, rumors = await following(turns)
    await wait_for(lambda: (entry(rumors, "v2") or {}).get("state") == "uncared")
    assert [(q, kw["threshold"], kw["top_k"]) for _, q, kw in mind_queries(engine, "v2")] == [
        ("bread", 0.5, engine.config.top_k), ("ale", 0.5, engine.config.top_k),
    ]
    assert turns.views == []
    await engine.stop()


@pytest.mark.asyncio
async def test_a_listener_who_cares_but_is_busy_shows_when_they_hear_it(turns):
    contex = StubContex({"bread": [Match("utt:u-1", {}, 0.9)]})
    rumors = await stub_rumors(0.9)
    engine, _, _ = await setup("v1", "v2", rumors=rumors, contex=contex, config=Config(cooldown_seconds=5))
    engine.baselines = StubBaselines(0.5)
    engine._last_turn["v2"] = time.monotonic()
    engine.lineage.record("w-1", None, player=PID)
    engine.lineage.record("u-1", "w-1")
    await engine.deliver(Utterance("u-1", "v1", "V1", "square", "psst again", "w-1"), ["v2"])
    await wait_for(lambda: (entry(rumors, "v2") or {}).get("state") == "busy")
    assert entry(rumors, "v2")["until"] > time.time()
    await engine.stop()


@pytest.mark.asyncio
async def test_a_retelling_too_tired_to_wake_is_worn(turns):
    contex = StubContex({"bread": [Match("utt:u-1", {}, 0.505)]})
    engine, _, rumors = await following(turns, contex=contex, depth=3, fatigue_per_hop=0.01)
    await wait_for(lambda: (entry(rumors, "v2") or {}).get("state") == "worn")
    assert entry(rumors, "v2")["depth"] == 3
    await engine.stop()


@pytest.mark.asyncio
async def test_a_room_hop_runs_no_check(turns):
    engine, _, _ = await following(turns, listeners=("v2", "v3"))
    await asyncio.sleep(SETTLE)
    assert not mind_queries(engine, "v2") and not mind_queries(engine, "v3")
    await engine.stop()


@pytest.mark.asyncio
async def test_a_listener_whose_turn_does_not_retell_kept_it_quiet(turns):
    engine, minds, rumors = await following(turns, baselines=False)
    minds["v2"].results = [[Match("utt:u-1", {"id": "u-1", "text": "psst again"})]]
    push(minds["v2"])
    await wait_for(lambda: (entry(rumors, "v2") or {}).get("state") == "kept")
    await engine.stop()


@pytest.mark.asyncio
async def test_a_listener_whose_turn_warns_is_marked_warned_not_kept(turns, monkeypatch):
    async def warn(jev, view):
        return Decision(WARN, None, None, None)

    monkeypatch.setattr(engine_mod, "decide", warn)
    engine, minds, rumors = await following(turns, baselines=False)
    minds["v2"].results = [[Match("utt:u-1", {"id": "u-1", "text": "psst again"})]]
    push(minds["v2"])
    await wait_for(lambda: (entry(rumors, "v2") or {}).get("state") == "warned")
    await engine.stop()


@pytest.mark.asyncio
async def test_stop_cancels_pending_checks(turns):
    engine, _, _ = await following(turns, contex=HangingContex())
    await wait_for(lambda: mind_queries(engine, "v2"))
    task, = engine._following
    await engine.stop()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_a_room_listener_who_does_not_retell_stays_in_the_crowd(turns):
    engine, minds, rumors = await following(turns, baselines=False, listeners=("v2", "v3"))
    minds["v2"].results = [[Match("utt:u-1", {"id": "u-1", "text": "psst again"})]]
    push(minds["v2"])
    await wait_for(lambda: len(turns.views) == 1)
    await asyncio.sleep(SETTLE)
    assert entry(rumors, "v2") is None
    assert rumors.cards(PID)[0]["stopped"] == "It stopped at the square: 2 heard it and no one passed it on."
    await engine.stop()


async def moment_engine(rumors, *villagers):
    lineage, town = Lineage(), Town()
    feed = Feed(lineage, town, out=None)
    engine = Engine(Config(), town, None, None, None, lineage, Ids(), feed, mind_factory=FakeMind, rumors=rumors)
    for v in villagers:
        await engine.add_villager(v)
    lineage.record("w-1", None, player=PID)
    rumors.card("w-1").place = "square"
    return engine, feed


def moments(feed):
    return [(e.data["text"], e.data["tone"]) for e in feed.recent if e.kind == "moment"]


async def retell(engine, n, ids, **kw):
    engine.lineage.record(f"u-{n}", "w-1")
    await engine.deliver(Utterance(f"u-{n}", "v1", "V1", "square", "psst again", "w-1", **kw), ids)


@pytest.mark.asyncio
async def test_moments_mark_five_and_ten_hearers_once(turns):
    engine, feed = await moment_engine(await stub_rumors(0.9), *(make(f"v{i}") for i in range(1, 13)))
    await retell(engine, 1, ["v2", "v3", "v4", "v5"])
    assert [t for t, _ in moments(feed)] == ["5 people have heard your rumor now."]
    await retell(engine, 2, [f"v{i}" for i in range(6, 12)])
    await retell(engine, 3, ["v12"])
    assert [t for t, _ in moments(feed)] == [
        "5 people have heard your rumor now.", "10 people have heard your rumor now.",
    ]
    await engine.stop()


@pytest.mark.asyncio
async def test_reaching_an_influential_villager_or_a_new_place_is_a_moment(turns):
    priest = Villager("v2", "V2", "Priest", "calm", "none", [], {})
    engine, feed = await moment_engine(await stub_rumors(0.9), make("v1"), priest)
    engine.lineage.record("u-1", "w-1")
    await engine.deliver(Utterance("u-1", "v1", "V1", "tavern", "psst again", "w-1"), ["v2"])
    await engine.deliver(Utterance("u-1", "v1", "V1", "tavern", "psst again", "w-1"), ["v2"])
    assert moments(feed) == [
        ("Your rumor reached V2, and folk listen to the priest.", "good"),
        ("Your rumor has reached the tavern.", "good"),
    ]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_retelling_that_names_the_newcomer_is_a_bad_moment(turns):
    engine, feed = await moment_engine(await stub_rumors(0.9), make("v1"), make("v2"))
    await retell(engine, 1, ["v2"], names_newcomer=True)
    await retell(engine, 2, ["v2"], names_newcomer=True)
    assert moments(feed) == [("Your rumor turned against you: V1 says a newcomer started it.", "bad")]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_rumor_scene_is_a_moment_once_two_have_spoken(turns):
    engine, feed = await moment_engine(await stub_rumors(0.9), make("v1"), make("v2"))
    opening = Utterance("u-1", "v1", "V1", "square", "psst again", "w-1")
    scene = engine.scenes.start(opening, "v2", "w-1", "w-1")
    engine._show_scene(scene)
    assert moments(feed) == []
    scene.people.add("v2")
    engine._show_scene(scene)
    engine._show_scene(scene)
    assert moments(feed) == [("Your rumor started a conversation at the square.", "good")]
    await engine.stop()


@pytest.mark.asyncio
async def test_an_off_topic_line_is_no_moment(turns):
    engine, feed = await moment_engine(await stub_rumors(0.1), make("v1"), make("v2"))
    await retell(engine, 1, ["v2"], names_newcomer=True)
    assert moments(feed) == []
    await engine.stop()


@pytest.mark.asyncio
async def test_an_answer_carries_how_taken_the_villager_is_with_the_rumor(talks):
    engine, minds, feed = await setup("v1")
    minds["v1"].results = [[], []]
    talks.plan = [ANSWER, ANSWER]
    engine.rapport.sour("v1", PID, 0.5)
    engine.rapport.move("v1", PID, "w-1", 0.6)
    whisper(engine, "v1", "w-1")
    await wait_for(lambda: statuses(feed))
    reply_whisper(engine, "v1", "w-2", "w-1")
    await wait_for(lambda: len(talks.moods) == 2)
    await engine.stop()
    first, second = talks.moods
    assert first.startswith("eager to hear more") and "fond of the newcomer" in first and "Greet the newcomer" in first
    assert second == "eager to hear more"


@pytest.mark.asyncio
async def test_engine_fills_taken_from_interest(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[]]
    engine.rapport.move("v1", PID, "w-1", 0.6)
    whisper(engine, "v1", "w-1")
    await wait_for(lambda: len(turns.views) == 1)
    await engine.stop()
    assert turns.views[0].taken == "eager"


@pytest.mark.asyncio
async def test_taken_follows_a_retelling_back_to_the_players_rumor(turns):
    engine, _, _ = await setup("v1")
    engine.facts.record(Utterance("w-2", NEWCOMER, "A newcomer", "square", "psst", None, conversation="w-1"))
    engine.lineage.record("w-2", None, player=PID)
    engine.lineage.record("u-9", "w-2")
    engine.rapport.move("v1", PID, "w-1", -0.5)
    assert engine.taken_by("v1", "u-9") == "put off"
    assert engine.taken_by("v1", "u-unrelated") is None
    await engine.stop()


@pytest.mark.asyncio
async def test_a_villager_who_only_heard_the_rumor_second_hand_has_no_taken_word(turns):
    engine, minds, _ = await setup("v1")
    minds["v1"].results = [[]]
    engine.rapport.sour("v1", PID, 0.5)
    engine.rapport.move("v2", PID, "w-1", 0.6)
    whisper(engine, "v1", "w-1")
    await wait_for(lambda: len(turns.views) == 1)
    await engine.stop()
    assert turns.views[0].taken is None and engine.taken_by("v1", "w-1") is None
    assert engine.taken_by("v2", "w-1") == "eager"


@pytest.mark.asyncio
async def test_a_reply_that_fails_keeps_the_greeting_for_the_next(talks, monkeypatch):
    engine, minds, feed = await setup("v1")
    minds["v1"].results = [[], [], []]
    talks.plan = [ANSWER, ANSWER, ANSWER]
    engine.rapport.sour("v1", PID, 0.5)
    act = talks.act

    async def failing_once(decision, villager, **kw):
        await act(decision, villager, **kw)
        if len(talks.moods) == 1:
            raise RuntimeError("haiku down")

    monkeypatch.setattr(engine_mod, "act", failing_once)
    whisper(engine, "v1", "w-1")
    await wait_for(lambda: len(talks.moods) == 1 and not engine._turning)
    engine._last_turn.clear()
    reply_whisper(engine, "v1", "w-2", "w-1")
    await wait_for(lambda: len(talks.moods) == 2 and not engine._turning)
    engine._last_turn.clear()
    reply_whisper(engine, "v1", "w-3", "w-1")
    await wait_for(lambda: len(talks.moods) == 3)
    await engine.stop()
    assert ["Greet the newcomer" in m for m in talks.moods] == [True, True, False]
