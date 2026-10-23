import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill import game as game_mod
from examples.rumor_mill.actions import IGNORE, LEAVE_TOWN, MOVE
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.game import MEMORY_PLAYERS, Game, Summary
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.judge import Judge
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.player import Player
from examples.rumor_mill.players import Players
from examples.rumor_mill.tests.helpers import PID, FakeMind, StubClient, goal_maker, make, make_player, wait_for
from examples.rumor_mill.town import PLACES, Town
from examples.rumor_mill.usage import Usage
from examples.rumor_mill.writer import Writer

GOALS = [("cat is a spy", 40)]
MEETING = "The village meets in the square and runs the newcomer out of town."


async def start(*vids, **cfg):
    town, lineage, lines = Town(), Lineage(), []
    feed = Feed(lineage, town, out=lines.append)
    game = Game()
    settings = dict(day_seconds=0.05, suspicion_check_days=0.2, run_out_checks=2, suspicion_decay_per_day=0.0)
    config = Config(**{**settings, **cfg})
    engine = Engine(
        config, town, None, None, None, lineage, Ids(), feed,
        mind_factory=FakeMind, game=game, summary_source=lambda p: GOALS,
    )
    player = Player(engine, town, lineage, Ids(), feed, config, player_id=PID)
    engine.players.add(player)
    for vid in vids:
        await engine.add_villager(make(vid))
    return engine, player, feed, lines


def script(engine, scores):
    """Set v1's score before each check from scores (then zero); returns the list of checks seen.

    Needs a positive suspicion_decay_per_day so the reset to zero works.
    """
    seen = []
    decay, suspicion = engine.suspicion.decay, engine.suspicion

    def scripted(days):
        decay(100)
        score = scores[len(seen)] if len(seen) < len(scores) else 0.0
        seen.append(days)
        suspicion.bump("v1", PID, score, "whispered to them directly")

    suspicion.decay = scripted
    return seen


class StubRoutine:
    """Sends each villager to one place at every part of the day."""

    def __init__(self, places):
        self.places = places

    def place(self, vid, phase):
        return self.places.get(vid)


def record_moves(monkeypatch, engine, delay=0.0):
    """Replace act with one that moves the villager after delay and records (vid, decision, share_chance, time)."""
    moves = []

    async def act(decision, v, *, town, share_chance=0.0, **_):
        moves.append((v.id, decision, share_chance, time.monotonic()))
        await asyncio.sleep(delay)
        town.move(v.id, decision.target)

    monkeypatch.setattr(engine_mod, "act", act)
    return moves


def kinds(feed, kind):
    return [e for e in feed.replay(PID) if e.kind == kind]


@pytest.mark.asyncio
async def test_run_out_after_consecutive_checks_at_the_level():
    engine, player, feed, lines = await start("v1", suspicion_decay_per_day=1.0)
    seen = script(engine, [0.5] * 10)
    await engine.run()
    await asyncio.wait_for(player.finished.wait(), 2)
    assert player.state == "lost"
    assert len(seen) == engine.config.run_out_checks
    assert [e.kind for e in feed.replay(PID)][-2:] == ["system", "gameover"]
    assert feed.replay(PID)[-2].line == MEETING
    await engine.stop()


@pytest.mark.asyncio
async def test_a_dip_resets_the_count():
    engine, player, _, _ = await start("v1", suspicion_decay_per_day=1.0)
    seen = script(engine, [0.5, 0.0, 0.5, 0.5])
    await engine.run()
    await asyncio.wait_for(player.finished.wait(), 2)
    assert len(seen) == 4
    await engine.stop()


@pytest.mark.asyncio
async def test_label_event_only_when_it_changes():
    engine, player, feed, lines = await start("v1", run_out_checks=10, suspicion_decay_per_day=1.0)
    seen = script(engine, [0.0, 0.5, 0.5, 0.0])
    await engine.run()
    await wait_for(lambda: len(seen) >= 6)
    await engine.stop()
    heat = kinds(feed, "heat")
    assert [e.data for e in heat] == [{"label": "hunted"}, {"label": "quiet"}]
    assert [e.line for e in heat] == ["The village is hunting for you.", "Folk are not talking about you."]
    clock = {e.line for e in kinds(feed, "time")}
    assert [line for line in lines if line not in clock] == [e.line for e in heat]
    assert player.state == "playing"


@pytest.mark.asyncio
async def test_loss_summary_names_what_gave_you_away_and_includes_goal_progress():
    engine, player, feed, lines = await start("v1", "v2", "v3")
    s = engine.suspicion
    s.bump("v1", PID, 0.9, "whispered to them directly")
    s.bump("v2", PID, 0.6, "was warned about the newcomer")
    s.bump("v3", PID, 0.5, "tried to whisper again")
    s.bump("v3", PID, 0.1, "a reason the summary has no words for")
    engine.town.remove("v2")
    await engine.run()
    await asyncio.wait_for(player.finished.wait(), 2)
    assert player.summary == Summary(
        "lost", GOALS, ["V1 heard it from you directly.", "V2 was warned about you.", "V3 caught you whispering to them again."],
    )
    [over] = kinds(feed, "gameover")
    assert over.data == {"outcome": "lost", "goals": [["cat is a spy", 40]], "reasons": player.summary.reasons}
    assert not any(n in text for n in ("0.9", "0.6") for text in (str(over.data), over.line))
    assert lines[-1].splitlines()[1:] == ["cat is a spy: 40%", *player.summary.reasons]
    await engine.stop()


@pytest.mark.asyncio
async def test_unknown_reason_reads_as_growing_suspicion():
    engine, player, _, _ = await start("v1")
    engine.suspicion.bump("v1", PID, 0.9, "something new")
    await engine.run()
    await asyncio.wait_for(player.finished.wait(), 2)
    assert player.summary.reasons == ["V1 grew suspicious of you."]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failed_check_is_logged_and_the_next_one_can_end_the_game(caplog):
    engine, player, _, _ = await start("v1")
    calls = []

    def flaky(p):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("judge down")
        return GOALS

    engine.summary_source = flaky
    engine.suspicion.bump("v1", PID, 0.9, "whispered to them directly")
    await engine.run()
    await asyncio.wait_for(player.finished.wait(), 2)
    assert (player.state, player.summary.goals, len(calls)) == ("lost", GOALS, 2)
    assert "town check failed" in caplog.text and "judge down" in caplog.text
    await engine.stop()


@pytest.mark.asyncio
async def test_empty_town_does_not_lose_by_division():
    engine, player, feed, _ = await start()
    seen = []
    decay = engine.suspicion.decay
    engine.suspicion.decay = lambda days: (seen.append(days), decay(days))
    await engine.run()
    await wait_for(lambda: len(seen) >= 4)
    await engine.stop()
    assert player.state == "playing"
    assert kinds(feed, "heat") == []


def test_a_players_game_ends_once():
    player, *_ = make_player("Marta")
    lost = Summary("lost", GOALS, ["V1 heard it from you directly."])
    player.end(lost)
    player.end(Summary("won", GOALS, []))
    assert (player.state, player.summary, player.over()) == ("lost", lost, True)
    assert [e.data["outcome"] for e in player.feed.replay(PID) if e.kind == "gameover"] == ["lost"]


def test_nothing_reaches_a_player_after_their_game_is_over():
    lineage, seen_a = Lineage(), []
    lineage.record("w-1", None, player="p-a")
    lineage.record("w-2", None, player="p-b")
    feed = Feed(lineage, Town(), out=None)
    feed.sinks.append(feed.view("p-a", seen_a.append))
    feed.on_game_over(Summary("lost", GOALS, []), player="p-a")
    feed.close("p-a")
    assert feed.say("late", player="p-a") is None
    feed.on_whisper_status("w-1", "v1", "Bert", "kept")
    feed.on_time(2, "morning")
    feed.on_whisper_status("w-2", "v1", "Bert", "kept")
    assert seen_a[-1].kind == "gameover" and feed.replay("p-a")[-1].kind == "gameover"
    assert [e.kind for e in feed.replay("p-b")][-2:] == ["time", "whisper_status"]


@pytest.mark.asyncio
async def test_whisper_after_game_over_is_refused():
    player, _, lines, engine, lineage = make_player("Marta")
    player.end(Summary("lost", [], []))
    assert await player.whisper_to("v1", "hello") == "The game is over."
    assert await player.whisper("Marta", "hello") == "The game is over."
    await asyncio.sleep(0.05)
    assert engine.delivered == []
    assert not lineage.is_player_rooted("w-1")
    assert engine.suspicion.score("v1", PID) == 0.0
    assert player._tasks == set()
    assert len(lines) == 1


GOAL = Goal("g", "cat is a spy", "belief", ("cat spy",), "Does it believe?", 0.5, hold_checks=2)


@pytest.mark.asyncio
async def test_judge_skips_the_goals_of_a_player_whose_game_is_over():
    player, *_ = make_player()
    town = Town()
    judge = Judge(None, None, town, Config(day_seconds=0.01), player.feed, Game(), maker=goal_maker(town))
    player.goals = [GOAL]
    player.end(Summary("lost", [], []))
    everyone = Players(player.feed)
    everyone.add(player)
    everyone.add(SimpleNamespace(id="p-2", owner="o-2", goals=[], met=set(), whispered_at=None, over=lambda: False))
    checked = []

    async def check(goal):
        checked.append(goal.id)
        return 0.0

    judge.check = check
    running = asyncio.create_task(judge.run(everyone))
    await wait_for(lambda: len(checked) >= 2)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert set(checked) == {judge.world.id}


def test_memory_starts_at_the_oldest_remembered_move():
    game = Game(memory_moves=2)
    game.moved(PID)
    assert game.memory_since() is None
    game.moved(PID)
    first = game.memory_since()
    game.moved(PID)
    assert first is not None and game.memory_since() >= first


def test_memory_spans_the_moves_of_every_active_player():
    game = Game(memory_moves=2)
    game.moved("p-a")
    game.moved("p-a")
    first = game.memory_since()
    assert first is not None
    game.moved("p-b")
    assert game.memory_since() is None
    game.moved("p-b")
    assert game.memory_since() == first


def test_memory_stays_capped_beyond_the_most_players_it_counts():
    game = Game(memory_moves=2)
    for i in range(2 * MEMORY_PLAYERS):
        game.moved(f"p-{i % (MEMORY_PLAYERS + 1)}")
    assert game.memory_since() is not None


@pytest.mark.asyncio
async def test_an_idle_player_stops_widening_memory():
    game = Game(memory_moves=1, day_seconds=0.01)
    game.moved("p-a")
    game.moved("p-b")
    await asyncio.sleep(0.02)
    game.moved("p-a")
    assert game.memory_since() is not None


def test_the_clock_runs_through_the_day_and_into_the_next():
    game = Game()
    assert (game.day, game.phase) == (1, "morning")
    seen = []
    for _ in range(4):
        game.tick()
        seen.append((game.day, game.phase))
    assert seen == [(1, "afternoon"), (1, "evening"), (1, "night"), (2, "morning")]


@pytest.mark.asyncio
async def test_the_engine_announces_each_part_of_the_day():
    engine, _, feed, _ = await start("v1", day_seconds=0.04)
    game = engine.game
    await engine.run()
    await asyncio.sleep(0.05)
    await engine.stop()
    assert [(e.data["day"], e.data["phase"]) for e in kinds(feed, "time")][:3] == [
        (1, "afternoon"), (1, "evening"), (1, "night"),
    ]
    assert (game.day, game.phase) != (1, "morning")


@pytest.mark.asyncio
async def test_the_clock_walks_villagers_to_their_routine_place():
    engine, _, _, _ = await start("v1", "v2", day_seconds=0.2)
    engine.routine = StubRoutine({"v1": "mill", "v2": "tavern"})
    await engine.run()
    await wait_for(lambda: (engine.town.place_of("v1"), engine.town.place_of("v2")) == ("mill", "tavern"))
    await engine.stop()


@pytest.mark.asyncio
async def test_a_villager_in_a_turn_a_scene_or_a_chosen_move_stays_put(monkeypatch):
    vids = ("v1", "v2", "v3", "v4")
    engine, _, _, _ = await start(*vids)
    engine.routine = StubRoutine(dict.fromkeys(vids, "mill"))
    engine._turning.add("v1")
    monkeypatch.setattr(engine.scenes, "of", lambda vid: object() if vid == "v2" else None)
    engine._chose_move.add("v3")
    await engine._walk("morning", 0.0)
    assert [engine.town.place_of(v) for v in vids] == ["square", "square", "square", "mill"]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failing_round_is_logged(caplog):
    engine, _, _, _ = await start("v1")

    class Broken:
        def place(self, vid, phase):
            raise RuntimeError("no places")

    engine.routine = Broken()
    with caplog.at_level(logging.ERROR):
        await engine._walk("morning", 0.0)
    assert "routine round failed" in caplog.text
    await engine.stop()


@pytest.mark.asyncio
async def test_a_departed_villager_is_not_moved(caplog):
    vids = ("v2", "v3", "v4", "v5", "v6", "v1")
    engine, _, _, _ = await start(*vids)
    engine.routine = StubRoutine(dict.fromkeys(vids, "mill"))
    walk = asyncio.create_task(engine._walk("morning", 0.4))
    await wait_for(lambda: engine.town.place_of("v2") == "mill")
    engine.town.remove("v1")
    with caplog.at_level(logging.ERROR):
        await walk
    assert not any("v1" in [v.id for v in engine.town.present(p)] for p in PLACES)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_routine_move_may_carry_the_latest_news(monkeypatch):
    engine, _, _, _ = await start("v1")
    engine.routine = StubRoutine({"v1": "mill"})
    moves = record_moves(monkeypatch, engine)
    latest = Match("k1", {"id": "u-1", "text": "the miller cheats"}, clearance=0.05)
    engine._latest["v1"] = latest
    await engine._walk("morning", 0.0)
    [(vid, decision, share, _)] = moves
    assert (vid, decision.action, decision.source, decision.target) == ("v1", MOVE, latest, "mill")
    assert share == engine.share_chance("v1", latest)
    await engine.stop()


@pytest.mark.asyncio
async def test_a_routine_move_carries_fresh_news_once(monkeypatch):
    engine, _, _, _ = await start("v1", "v2")
    engine.routine = StubRoutine({"v1": "mill", "v2": "mill"})
    moves = record_moves(monkeypatch, engine)
    engine._latest["v1"] = Match("k1", {"id": "u-1", "text": "the miller cheats"}, clearance=0.05)
    engine._latest["v2"] = Match("k2", {"id": "u-2", "text": "the well is dry"}, clearance=-0.01)
    await engine._walk("morning", 0.0)
    engine.routine = StubRoutine({"v1": "tavern", "v2": "tavern"})
    await engine._walk("evening", 0.0)
    assert [(vid, d.source and d.source.data_key) for vid, d, *_ in moves] == [
        ("v1", "k1"), ("v2", None), ("v1", None), ("v2", None),
    ]
    await engine.stop()


@pytest.mark.asyncio
async def test_routine_moves_come_in_batches_of_five_across_the_window(monkeypatch):
    vids = [f"v{i}" for i in range(12)]
    engine, _, _, _ = await start(*vids)
    engine.routine = StubRoutine(dict.fromkeys(vids, "mill"))
    moves = record_moves(monkeypatch, engine)
    spread = 0.6
    await engine._walk("morning", spread)
    times = [t for *_, t in moves]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert [vid for vid, *_ in moves] == vids
    assert all(gaps[i] >= 0.9 * spread / 3 for i in (4, 9))
    assert all(g < 0.5 * spread / 3 for i, g in enumerate(gaps) if i not in (4, 9))
    await engine.stop()


@pytest.mark.asyncio
async def test_phases_stay_evenly_spaced_with_a_routine(monkeypatch):
    vids = [f"v{i}" for i in range(12)]
    engine, _, feed, _ = await start(*vids, day_seconds=0.4)
    engine.routine = StubRoutine(dict.fromkeys(vids, "mill"))
    record_moves(monkeypatch, engine, delay=5.0)
    ticks = []
    on_time = feed.on_time
    monkeypatch.setattr(feed, "on_time", lambda day, phase: (ticks.append(time.monotonic()), on_time(day, phase)))
    await engine.run()
    await wait_for(lambda: len(ticks) >= 4)
    await engine.stop()
    assert all(b - a < 1.0 for a, b in zip(ticks, ticks[1:4]))


@pytest.mark.asyncio
async def test_run_out_ends_only_that_players_game():
    engine, pa, feed, _ = await start("v1")
    pb = Player(engine, engine.town, engine.lineage, Ids(), feed, engine.config, player_id="p-b")
    engine.players.add(pb)
    engine.suspicion.bump("v1", PID, 0.9, "whispered to them directly")
    await engine.run()
    await asyncio.wait_for(pa.finished.wait(), 2)
    await engine.stop()
    assert pa.state == "lost" and pb.state == "playing"
    assert MEETING in [e.line for e in feed.replay(PID)] and MEETING not in [e.line for e in feed.replay("p-b")]


@pytest.mark.asyncio
async def test_villagers_keep_their_places_while_nobody_is_playing():
    engine, player, _, _ = await start("v1")
    engine.routine = StubRoutine({"v1": "mill"})
    player.end(Summary("lost", GOALS, []))
    await engine._walk("morning", 0.0)
    assert engine.town.place_of("v1") == "square"
    await engine.stop()


def test_the_clock_says_how_far_through_the_day_it_is(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(game_mod.time, "time", lambda: now[0])
    game = Game(day_seconds=120.0)
    now[0] += 15
    assert game.clock() == {"day": 1, "phase": "morning", "fraction": 0.125, "day_seconds": 120.0}
    game.tick()
    now[0] += 60
    assert game.clock()["fraction"] == 0.5


def talking(engine, player, vid="v1"):
    """player in an open conversation with vid where they both stand, having just spoken to them."""
    engine.conversations[(vid, player.id)] = {"id": f"w-{player.id}", "answered": [f"w-{player.id}"]}
    player._said_at[vid] = time.time()


def deciding(monkeypatch, action=IGNORE, target=None):
    """Make every turn take action (at target) and record each turn's view."""
    views = []

    async def decide(jev, view):
        views.append(view)
        return Decision(action, None, target, None)

    monkeypatch.setattr(engine_mod, "decide", decide)
    return views


@pytest.mark.asyncio
async def test_the_routine_leaves_a_villager_someone_is_talking_with():
    engine, player, _, _ = await start("v1", "v2")
    engine.routine = StubRoutine({"v1": "mill", "v2": "mill"})
    talking(engine, player)
    await engine._walk("morning", 0.0)
    assert (engine.town.place_of("v1"), engine.town.place_of("v2")) == ("square", "mill")
    await engine.stop()


@pytest.mark.asyncio
async def test_a_villager_stays_while_either_of_two_players_holds_them():
    engine, pa, feed, _ = await start("v1")
    pb = Player(engine, engine.town, engine.lineage, Ids(), feed, engine.config, player_id="p-b")
    engine.players.add(pb)
    engine.routine = StubRoutine({"v1": "mill"})
    talking(engine, pb)
    await engine._walk("morning", 0.0)
    assert engine.town.place_of("v1") == "square"
    pb.walk("well")
    await engine._walk("morning", 0.0)
    assert engine.town.place_of("v1") == "mill"
    await engine.stop()


@pytest.mark.asyncio
async def test_a_held_villagers_turn_cannot_walk_off_unless_every_holder_annoys_them(monkeypatch):
    views = deciding(monkeypatch)
    engine, pa, feed, _ = await start("v1")
    pb = Player(engine, engine.town, engine.lineage, Ids(), feed, engine.config, player_id="p-b")
    engine.players.add(pb)
    talking(engine, pa)
    talking(engine, pb)
    engine.suspicion.bump("v1", PID, engine.config.warn_threshold, "whispered to them directly")
    engine.minds["v1"].queue.put_nowait(None)
    await wait_for(lambda: len(views) == 1)
    assert views[0].held
    pb.walk("well")
    engine._last_turn.clear()
    engine.minds["v1"].queue.put_nowait(None)
    await wait_for(lambda: len(views) == 2)
    assert not views[1].held
    await engine.stop()


async def storming(monkeypatch, action, target=None):
    """v1, annoyed with PID and held by them, takes action at its next turn while p-b looks on from the well."""
    views = deciding(monkeypatch, action, target)
    engine, pa, feed, _ = await start("v1")
    pb = Player(engine, engine.town, engine.lineage, Ids(), feed, engine.config, player_id="p-b")
    pb.walk("well")
    engine.players.add(pb)
    usage = Usage(engine.config)
    engine.writer = Writer(StubClient("I've heard quite enough from you.", "Farewell, all."), "haiku-test", usage)
    talking(engine, pa)
    talking(engine, pb)
    engine.suspicion.bump("v1", PID, engine.config.warn_threshold, "whispered to them directly")
    engine.minds["v1"].queue.put_nowait(None)
    await wait_for(lambda: views and not engine._turning)
    return engine, feed, usage


@pytest.mark.asyncio
async def test_an_annoyed_villager_who_walks_off_storms_off_on_the_player(monkeypatch):
    engine, feed, usage = await storming(monkeypatch, MOVE, "mill")
    stormed = kinds(feed, "stormed_off")
    assert [(e.data["name"], e.data["line"]) for e in stormed] == [("V1", "I've heard quite enough from you.")]
    assert stormed[0].line == 'V1 storms off: "I\'ve heard quite enough from you."'
    assert not [e for e in feed.replay("p-b") if e.kind == "stormed_off"]
    assert ("v1", PID) not in engine.conversations and ("v1", "p-b") in engine.conversations
    (bump,) = [e for e in engine.suspicion._evidence if e.reason == "stormed off"]
    assert (bump.player, bump.amount) == (PID, engine.config.suspicion_whisper_bump / 2)
    assert usage.cost(PID) > 0 and usage.cost("p-b") == 0
    assert engine.town.place_of("v1") == "mill"
    await engine.stop()


@pytest.mark.asyncio
async def test_an_annoyed_villager_who_leaves_town_storms_off_first(monkeypatch):
    engine, feed, _ = await storming(monkeypatch, LEAVE_TOWN)
    assert [e.data["line"] for e in kinds(feed, "stormed_off")] == ["I've heard quite enough from you."]
    assert "v1" not in engine.town
    await engine.stop()


@pytest.mark.asyncio
async def test_an_annoyed_villager_who_stays_does_not_storm_off(monkeypatch):
    engine, feed, _ = await storming(monkeypatch, IGNORE)
    assert kinds(feed, "stormed_off") == [] and ("v1", PID) in engine.conversations
    await engine.stop()


async def traced_engine():
    """An engine with v1 the subject and v2, v3 the marks of PID's goal, and rumors of PID's at 1 to 4 hops."""
    engine, player, feed, lines = await start("v1", "v2", "v3", suspicion_decay_per_day=1.0)
    for n in range(1, 5):
        parent = None
        for hop in range(n):
            uid = f"w{n}" if hop == 0 else f"w{n}-{hop}"
            engine.lineage.record(uid, parent, player=PID if hop == 0 else None)
            parent = uid
    spoken = []

    async def deny(villager, claim):
        spoken.append((villager.id, claim))
        return "Never happened."

    engine.writer = SimpleNamespace(deny=deny)
    goal = Goal(
        "gen-1", "t", "belief", ("hob steals",), "?", 0.0, audience=("v2", "v3"), subject="v1", owner=PID,
        claim_text="Hob steals geese",
    )
    return engine, goal, feed, spoken


@pytest.mark.asyncio
async def test_subject_hearing_owner_rumor_bumps_by_hops():
    engine, goal, _, _ = await traced_engine()
    bump = engine.config.suspicion_whisper_bump
    for uid, share in (("w1", 1), ("w2-1", 1 / 2), ("w3-2", 1 / 4), ("w4-3", 0)):
        engine.suspicion.decay(100)
        engine.traced(goal, "v1", uid)
        assert engine.suspicion.score("v1", PID) == pytest.approx(bump * share)
    engine.suspicion.decay(100)
    engine.traced(goal, "v1", "w1")
    engine.traced(goal, "v1", "w2")
    assert engine.suspicion.reasons(PID)[0] == "V1 traced a rumor back to you."
    await engine.stop()


@pytest.mark.asyncio
async def test_trace_bump_once_per_root():
    engine, goal, _, _ = await traced_engine()
    engine.traced(goal, "v1", "w3-1")
    engine.traced(goal, "v1", "w3-1")
    engine.traced(goal, "v1", "w3-2")
    assert engine.suspicion.score("v1", PID) == pytest.approx(engine.config.suspicion_whisper_bump / 2)
    await engine.stop()


@pytest.mark.asyncio
async def test_a_closer_memory_of_a_traced_root_tops_up_the_bump():
    engine, goal, _, _ = await traced_engine()
    bump = engine.config.suspicion_whisper_bump
    engine.traced(goal, "v1", "w4-3")
    assert engine.suspicion.score("v1", PID) == 0
    engine.traced(goal, "v1", "w4")
    assert engine.suspicion.score("v1", PID) == pytest.approx(bump)
    engine.traced(goal, "v1", "w3-2")
    engine.traced(goal, "v1", "w3")
    assert engine.suspicion.score("v1", PID) == pytest.approx(bump * 2)
    await engine.stop()


@pytest.mark.asyncio
async def test_a_second_crossing_for_the_same_goal_denies_nothing_more():
    engine, goal, _, spoken = await traced_engine()
    engine.suspicion.bump("v1", PID, engine.config.warn_threshold - 0.01, "x")
    engine.traced(goal, "v1", "w1")
    await wait_for(lambda: spoken)
    engine.suspicion.decay(100)
    engine.suspicion.bump("v1", PID, engine.config.warn_threshold - 0.01, "x")
    engine.traced(goal, "v1", "w2")
    await asyncio.sleep(0.05)
    assert len(spoken) == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_crossing_warn_threshold_denies_to_marks_once():
    engine, goal, feed, spoken = await traced_engine()
    engine.suspicion.bump("v1", PID, engine.config.warn_threshold - 0.01, "x")
    engine.traced(goal, "v1", "w1")
    await wait_for(lambda: engine.minds["v2"].heard)
    key, data = engine.minds["v2"].heard[0]
    assert data["text"] == "V1 said: Never happened." and spoken == [("v1", "Hob steals geese")]
    assert engine.minds["v3"].heard and not engine.minds["v1"].heard
    await wait_for(lambda: kinds(feed, "denied"))
    assert [e.data["line"] for e in kinds(feed, "denied")] == ["Never happened."]
    engine.traced(goal, "v1", "w2")
    await asyncio.sleep(0.05)
    assert len(spoken) == 1 and len(engine.minds["v2"].heard) == 1
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failed_denial_is_logged(caplog):
    engine, goal, feed, _ = await traced_engine()

    async def boom(villager, claim):
        raise RuntimeError("down")

    engine.writer = SimpleNamespace(deny=boom)
    engine.suspicion.bump("v1", PID, engine.config.warn_threshold - 0.01, "x")
    with caplog.at_level(logging.ERROR):
        engine.traced(goal, "v1", "w1")
        await wait_for(lambda: "denial" in caplog.text)
    assert not kinds(feed, "denied")
    await engine.stop()
