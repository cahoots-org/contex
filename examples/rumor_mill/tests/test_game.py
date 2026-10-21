import asyncio
import logging
import time

import pytest

from examples.rumor_mill import engine as engine_mod
from examples.rumor_mill.actions import MOVE
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.engine import Engine
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.judge import Judge
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.tests.helpers import FakeMind, make, make_player, wait_for
from examples.rumor_mill.town import PLACES, Town

GOALS = [("cat is a spy", 40)]
MEETING = "The village meets in the square and runs the newcomer out of town."


async def start(*vids, **cfg):
    town, lineage, lines = Town(), Lineage(), []
    feed = Feed(lineage, town, out=lines.append)
    game = Game(feed)
    settings = dict(day_seconds=0.05, suspicion_check_days=0.2, run_out_checks=2, suspicion_decay_per_day=0.0)
    engine = Engine(
        Config(**{**settings, **cfg}), town, None, None, None, lineage, Ids(), feed,
        mind_factory=FakeMind, game=game, summary_source=lambda: GOALS,
    )
    for vid in vids:
        await engine.add_villager(make(vid))
    return engine, game, feed, lines


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
        suspicion.bump("v1", score, "whispered to them directly")

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
    return [e for e in feed.recent if e.kind == kind]


@pytest.mark.asyncio
async def test_run_out_after_consecutive_checks_at_the_level():
    engine, game, feed, lines = await start("v1", suspicion_decay_per_day=1.0)
    seen = script(engine, [0.5] * 10)
    await engine.run()
    await asyncio.wait_for(game.finished.wait(), 2)
    assert game.state == "lost"
    assert len(seen) == engine.config.run_out_checks
    assert [e.kind for e in feed.recent][-2:] == ["system", "gameover"]
    assert feed.recent[-2].line == MEETING
    await engine.stop()


@pytest.mark.asyncio
async def test_a_dip_resets_the_count():
    engine, game, _, _ = await start("v1", suspicion_decay_per_day=1.0)
    seen = script(engine, [0.5, 0.0, 0.5, 0.5])
    await engine.run()
    await asyncio.wait_for(game.finished.wait(), 2)
    assert len(seen) == 4
    await engine.stop()


@pytest.mark.asyncio
async def test_label_event_only_when_it_changes():
    engine, game, feed, lines = await start("v1", run_out_checks=10, suspicion_decay_per_day=1.0)
    seen = script(engine, [0.0, 0.5, 0.5, 0.0])
    await engine.run()
    await wait_for(lambda: len(seen) >= 6)
    await engine.stop()
    heat = kinds(feed, "heat")
    assert [e.data for e in heat] == [{"label": "hunted"}, {"label": "quiet"}]
    assert [e.line for e in heat] == ["The village is hunting for you.", "Folk are not talking about you."]
    clock = {e.line for e in kinds(feed, "time")}
    assert [line for line in lines if line not in clock] == [e.line for e in heat]
    assert game.state == "playing"


@pytest.mark.asyncio
async def test_loss_summary_names_what_gave_you_away_and_includes_goal_progress():
    engine, game, feed, lines = await start("v1", "v2", "v3")
    s = engine.suspicion
    s.bump("v1", 0.9, "whispered to them directly")
    s.bump("v2", 0.6, "was warned about the newcomer")
    s.bump("v3", 0.5, "tried to whisper again")
    s.bump("v3", 0.1, "a reason the summary has no words for")
    engine.town.remove("v2")
    await engine.run()
    await asyncio.wait_for(game.finished.wait(), 2)
    assert game.summary == Summary(
        "lost", GOALS, ["V1 heard it from you directly.", "V2 was warned about you.", "V3 caught you whispering to them again."],
    )
    [over] = kinds(feed, "gameover")
    assert over.data == {"outcome": "lost", "goals": [["cat is a spy", 40]], "reasons": game.summary.reasons}
    assert not any(n in text for n in ("0.9", "0.6") for text in (str(over.data), over.line))
    assert lines[-1].splitlines()[1:] == ["cat is a spy: 40%", *game.summary.reasons]
    await engine.stop()


@pytest.mark.asyncio
async def test_unknown_reason_reads_as_growing_suspicion():
    engine, game, _, _ = await start("v1")
    engine.suspicion.bump("v1", 0.9, "something new")
    await engine.run()
    await asyncio.wait_for(game.finished.wait(), 2)
    assert game.summary.reasons == ["V1 grew suspicious of you."]
    await engine.stop()


@pytest.mark.asyncio
async def test_a_failed_check_is_logged_and_the_next_one_can_end_the_game(caplog):
    engine, game, _, _ = await start("v1")
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("judge down")
        return GOALS

    engine.summary_source = flaky
    engine.suspicion.bump("v1", 0.9, "whispered to them directly")
    await engine.run()
    await asyncio.wait_for(game.finished.wait(), 2)
    assert (game.state, game.summary.goals, len(calls)) == ("lost", GOALS, 2)
    assert "town check failed" in caplog.text and "judge down" in caplog.text
    await engine.stop()


@pytest.mark.asyncio
async def test_empty_town_does_not_lose_by_division():
    engine, game, feed, _ = await start()
    seen = []
    decay = engine.suspicion.decay
    engine.suspicion.decay = lambda days: (seen.append(days), decay(days))
    await engine.run()
    await wait_for(lambda: len(seen) >= 4)
    await engine.stop()
    assert game.state == "playing"
    assert kinds(feed, "heat") == []


def test_nothing_prints_after_the_game_is_over():
    town, lines = Town(), []
    feed = Feed(Lineage(), town, out=lines.append)
    game = Game(feed)
    game.lose(Summary("lost", GOALS, ["V1 heard it from you directly."]))
    assert game.finished.is_set() and feed.closed
    printed, recorded = list(lines), list(feed.recent)
    assert len(printed) == 1 and recorded[-1].kind == "gameover"
    assert feed.emit("system", "late", text="late") is None
    feed.say("late")
    feed.on_heat("hunted")
    feed.on_delivery(Utterance("u-1", "v1", "V1", "tavern", "news", None), [])
    assert lines == printed and list(feed.recent) == recorded


@pytest.mark.asyncio
async def test_whisper_after_game_over_is_refused():
    player, _, lines, engine, lineage = make_player("Marta")
    player.game = Game(player.feed)
    player.game.lose(Summary("lost", [], []))
    assert await player.whisper_to("v1", "hello") == "The game is over."
    assert await player.whisper("Marta", "hello") == "The game is over."
    await asyncio.sleep(0.05)
    assert engine.delivered == []
    assert not lineage.is_player_rooted("w-1")
    assert engine.suspicion.score("v1") == 0.0
    assert player._brush_offs == set()
    assert len(lines) == 1


def judge_with_game(progress):
    feed = Feed(Lineage(), Town(), out=None)
    game = Game(feed)
    judge = Judge(None, None, Town(), Config(day_seconds=0.01), feed, game)
    values = iter(progress)

    async def check(goal):
        return next(values)

    judge.check = check
    return judge, game, feed


GOAL = Goal("g", "cat is a spy", "belief", ("cat spy",), "Does it believe?", 0.5, hold_checks=2)


@pytest.mark.asyncio
async def test_winning_through_the_judge_ends_the_game_once():
    judge, game, feed = judge_with_game([0.5, 1.0, 1.0])
    await asyncio.wait_for(judge.run([GOAL]), 2)
    assert game.state == "won"
    assert game.summary == Summary("won", [("cat is a spy", 100)], [])
    game.lose(Summary("lost", [], ["late"]))
    assert game.state == "won"
    assert len(kinds(feed, "gameover")) == 1


@pytest.mark.asyncio
async def test_judge_stops_once_the_game_is_over():
    judge, game, _ = judge_with_game([])
    game.lose(Summary("lost", [], []))
    await asyncio.wait_for(judge.run([GOAL]), 2)
    assert judge.progress == {}


def test_first_finish_wins_the_race():
    feed = Feed(Lineage(), Town(), out=None)
    game = Game(feed)
    lost = Summary("lost", GOALS, ["V1 heard it from you directly."])
    game.lose(lost)
    game.win(Summary("won", GOALS, []))
    assert (game.state, game.summary) == ("lost", lost)
    assert [e.data["outcome"] for e in kinds(feed, "gameover")] == ["lost"]


def test_memory_starts_at_the_oldest_remembered_move():
    game = Game(None, memory_moves=2)
    game.moved()
    assert game.memory_since() is None
    game.moved()
    first = game.memory_since()
    game.moved()
    assert first is not None and game.memory_since() >= first


def test_the_clock_runs_through_the_day_and_into_the_next():
    game = Game(None)
    assert (game.day, game.phase) == (1, "morning")
    seen = []
    for _ in range(4):
        game.tick()
        seen.append((game.day, game.phase))
    assert seen == [(1, "afternoon"), (1, "evening"), (1, "night"), (2, "morning")]


@pytest.mark.asyncio
async def test_the_engine_announces_each_part_of_the_day():
    engine, game, feed, _ = await start("v1", day_seconds=0.04)
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
    spread = 0.3
    await engine._walk("morning", spread)
    times = [t for *_, t in moves]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(moves) == 12
    assert [i for i, g in enumerate(gaps) if g >= 0.9 * spread / 3] == [4, 9]
    await engine.stop()


@pytest.mark.asyncio
async def test_phases_stay_evenly_spaced_with_a_routine(monkeypatch):
    vids = [f"v{i}" for i in range(12)]
    engine, game, feed, _ = await start(*vids, day_seconds=0.4)
    engine.routine = StubRoutine(dict.fromkeys(vids, "mill"))
    record_moves(monkeypatch, engine, delay=0.5)
    ticks = []
    on_time = feed.on_time
    monkeypatch.setattr(feed, "on_time", lambda day, phase: (ticks.append(time.monotonic()), on_time(day, phase)))
    await engine.run()
    await wait_for(lambda: len(ticks) >= 4)
    await engine.stop()
    phase_seconds = 0.4 / 4
    assert all(b - a < 1.8 * phase_seconds for a, b in zip(ticks, ticks[1:4]))
