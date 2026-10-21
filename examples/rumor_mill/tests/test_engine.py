import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill.actions import IGNORE, SPEAK, WARN
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.game import Game
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import Mind
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.player import Player
from examples.rumor_mill.tests.helpers import TIMEOUT, FakeMind, StubBaselines, StubContex, make, wait_for
from examples.rumor_mill.town import Town

SETTLE = 0.1


class RecordingFeed(Feed):
    def __init__(self):
        super().__init__(Lineage(), Town(), out=None)
        self.deliveries = []

    def on_delivery(self, utterance, listener_ids):
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


async def setup(*vids, cooldown=0.05, game=None):
    minds = {}

    def factory(v):
        minds[v.id] = FakeMind(v)
        return minds[v.id]

    feed = RecordingFeed()
    engine = Engine(
        Config(cooldown_seconds=cooldown), Town(), None, None, None, Lineage(), Ids(), feed,
        mind_factory=factory, game=game,
    )
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
async def test_stop_leaves_no_pending_tasks(turns):
    engine, minds, _ = await setup("v1", "v2", game=Game(RecordingFeed()))
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
    engine.lineage.record("w-1", None, player=True)
    engine.lineage.record("u-0", "w-1")
    engine.facts.record(Utterance("u-0", "v2", "V2", "square", "news", "w-1", names_newcomer=True))
    turns.strength = 1.0
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 1)
    view = turns.views[0]
    assert view.names_newcomer == view.new
    assert (view.suspicion, view.warn_threshold) == (0.0, engine.config.warn_threshold)
    assert engine.suspicion.score("v1") == engine.config.suspicion_hear_max

    def broken(*args):
        raise RuntimeError("rating down")

    engine.suspicion.rate = broken
    push(minds["v1"])
    await wait_for(lambda: len(turns.acts) == 2)
    assert turns.views[1].suspicion == engine.config.suspicion_hear_max
    assert "rating down" in caplog.text
    engine.suspect("v1", 0.1, "was warned about the newcomer", "u-1")
    assert engine.suspicion.score("v1") == pytest.approx(engine.config.suspicion_hear_max + 0.1)
    await engine.stop()


@pytest.mark.asyncio
async def test_no_turns_start_after_the_game_is_over(turns):
    game = Game(RecordingFeed())
    engine, minds, _ = await setup("v1", game=game)
    game.finished.set()
    push(minds["v1"])
    await asyncio.sleep(SETTLE)
    assert (turns.views, minds["v1"].polls) == ([], 0)
    await engine.stop()


@pytest.mark.asyncio
async def test_a_turn_still_deciding_when_the_game_ends_does_nothing(turns, monkeypatch):
    game = Game(RecordingFeed())
    engine, minds, _ = await setup("v1", game=game)
    engine.lineage.record("w-1", None, player=True)
    engine.lineage.record("u-0", "w-1")
    deciding, release = asyncio.Event(), asyncio.Event()

    async def slow_decide(jev, view):
        deciding.set()
        await release.wait()
        return Decision(IGNORE, None, None, None, 1.0)

    monkeypatch.setattr(engine_mod, "decide", slow_decide)
    push(minds["v1"])
    await asyncio.wait_for(deciding.wait(), TIMEOUT)
    game.finished.set()
    release.set()
    await asyncio.sleep(SETTLE)
    assert turns.acts == []
    assert engine.suspicion.score("v1") == 0.0
    assert engine._pending.get("v1") is None
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
    player = Player(engine, engine.town, lineage, Ids(), feed, engine.config)
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
    engine.lineage.record(wid, None, player=True)
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
    engine.lineage.record("w-1", None, player=True)
    engine.lineage.record("u-0", "w-1")
    engine.facts.record(Utterance("u-0", "v2", "V2", "square", "Drogo drinks", "w-1", style="exaggerated"))
    minds["v1"].results = [[Match("utt:u-0", {"id": "u-0", "text": "V2 said at the square: Drogo drinks"})]]
    push(minds["v1"])
    await wait_for(lambda: engine.jev.asked)
    assert engine.jev.asked == [False]
    assert engine.suspicion.score("v1") == 0.0
    await engine.stop()


@pytest.mark.asyncio
async def test_a_warning_asks_no_score_question(monkeypatch):
    async def no_act(*args, **kw):
        pass

    monkeypatch.setattr(engine_mod, "act", no_act)
    engine, minds, _ = await setup("v1")
    engine.jev = Rater()
    engine.facts.record(Utterance("u-0", "v2", "V2", "square", "Mind the newcomer", None, "warned",
                                  names_newcomer=True, action=WARN))
    engine.suspect("v1", 0.1, "was warned about the newcomer", "u-0")
    engine.hear_directly("v1", Match("utt:u-0", {"id": "u-0", "text": "V2 warned at the square: Mind the newcomer"}))
    await wait_for(lambda: engine.jev.asked)
    assert engine.jev.asked == [False]
    assert engine.suspicion.score("v1") == pytest.approx(0.1)
    await engine.stop()


def told(engine, uid, depth=0, audience="one", action=None):
    engine.facts.record(Utterance(uid, "v9", "V9", "tavern", "news", None, depth=depth, audience=audience, action=action))


@pytest.mark.asyncio
async def test_deep_retelling_does_not_wake(turns):
    engine, minds, _ = await setup("v1")
    told(engine, "u-deep", depth=3)
    minds["v1"].results = [[Match("k-deep", {"id": "u-deep", "text": "old news"}, 0.9, 0.025)]]
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
        [Match("k-room", {"id": "u-room", "text": "murmur"}, 0.9, 0.005)],
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
    stale = Match("k-stale", {"id": "u-stale", "text": "old news"}, 0.9, 0.01)
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
    stale = Match("k-stale", {"id": "u-stale", "text": "old news"}, 0.9, 0.01)
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
async def test_warnings_always_fresh(turns):
    engine, minds, _ = await setup("v1")
    warning = Utterance("u-warn", "v2", "V2", "tavern", "watch the newcomer", None, depth=4, action=WARN)
    engine.facts.record(warning)
    minds["v1"].results = [[]]
    await engine.deliver(warning, ["v1"], addressed=True)
    await wait_for(lambda: len(turns.views) == 1)
    assert [m.data_key for m in turns.views[0].new] == ["utt:u-warn"]
    await engine.stop()
