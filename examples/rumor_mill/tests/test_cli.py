import asyncio
from types import SimpleNamespace

import pytest

from examples.rumor_mill import cli
from examples.rumor_mill.cli import Command, parse_command
from examples.rumor_mill.config import Config
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.game import Summary
from examples.rumor_mill.goals import Goal, Trophy
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import Player, Refusal
from examples.rumor_mill.players import Players
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.tests.helpers import PID
from examples.rumor_mill.town import Town


def test_parse_command_forms():
    assert parse_command("Marta: hello there") == Command("whisper", "Marta", "hello there")
    assert parse_command("whisper Marta: hi") == Command("whisper", "Marta", "hi")
    assert parse_command("Old Bert: hello") == Command("whisper", "Old Bert", "hello")
    assert parse_command("villagers") == Command("villagers")
    assert parse_command("inspect Old Bert") == Command("inspect", "Old Bert")
    assert parse_command("go Tavern") == Command("go", text="tavern")
    assert parse_command("go ") == Command("unknown")
    assert parse_command("goals") == Command("goals")
    assert parse_command("listen") == Command("listen")
    assert parse_command("help") == Command("help")
    assert parse_command("usage") == Command("usage")
    assert parse_command("trophies") == Command("trophies")
    assert parse_command("quit") == Command("quit")
    assert parse_command("  quit  ") == Command("quit")
    assert parse_command("dance") == Command("unknown")
    assert parse_command("") == Command("unknown")
    assert parse_command(": nobody") == Command("unknown")


def make_walker():
    town, lineage, out = Town(), Lineage(), []
    feed = Feed(lineage, town, out=out.append)
    config = Config()
    player = Player(None, town, lineage, Ids(), feed, config, player_id=PID)
    return player, town, out


def scripted(lines):
    queue = list(lines)

    async def readline():
        if queue:
            return queue.pop(0)
        await asyncio.Event().wait()

    return readline


def village():
    town, out = Town(), []
    town.add(Villager("v1", "Marta", "baker", "calm", "none", [], {}))
    feed = Feed(Lineage(), town, out=out.append)
    return town, feed, out


@pytest.mark.asyncio
async def test_a_refused_whisper_prints_once(monkeypatch):
    town, feed, out = village()

    async def refuse(name, text):
        feed.on_refusal("v1", "Marta", "Not another word.")
        return Refusal("Marta turns away from you.")

    player = SimpleNamespace(whisper=refuse)
    monkeypatch.setattr(cli, "_readline", scripted(["Marta: hi\n", ""]))
    await cli.repl(player, town, feed)
    assert [line for line in out if "turns away" in line] == ['Marta turns away from you: "Not another word."']


@pytest.mark.asyncio
async def test_goals_prints_the_players_goals_the_world_goal_and_the_heat_label(monkeypatch):
    town, feed, out = village()
    suspicion = Suspicion(Config(), town)
    suspicion.bump("v1", PID, 0.9, "whispered to them directly")
    monkeypatch.setattr(cli, "_readline", scripted(["goals\n", ""]))
    goal = Goal("g", "cat is a spy", "belief", ("cat spy",), "q", 0.5)
    world = Goal("world-w", "Two thirds of the village believes the well is cursed", "belief", ("well",), "q", 2 / 3)
    judge = SimpleNamespace(progress={"g": 0.4, "world-w": 0.1}, world=world)
    await cli.repl(SimpleNamespace(id=PID, goals=[goal]), town, feed, judge, suspicion=suspicion)
    assert out[-3:] == [
        "cat is a spy: 40%", "World goal: Two thirds of the village believes the well is cursed: 10%",
        "The village is hunting for you.",
    ]


@pytest.mark.asyncio
async def test_the_repl_ends_with_the_summary_when_the_game_is_over(monkeypatch):
    town, feed, out = village()
    player = Player(None, town, feed.lineage, Ids(), feed, Config(), player_id=PID)
    monkeypatch.setattr(cli, "_readline", scripted([]))
    session = asyncio.create_task(cli.until_over(cli.repl(None, town, feed), player))
    await asyncio.sleep(0.01)
    player.end(Summary("lost", [("cat is a spy", 40)], ["Marta heard it from you directly."]))
    await asyncio.wait_for(session, 1)
    assert out[-1].splitlines()[1:] == ["cat is a spy: 40%", "Marta heard it from you directly."]


@pytest.mark.asyncio
async def test_an_error_in_the_session_propagates_out_of_until_over():
    town, feed, _ = village()

    async def broken():
        raise RuntimeError("whisper failed")

    with pytest.raises(RuntimeError, match="whisper failed"):
        player = Player(None, town, feed.lineage, Ids(), feed, Config(), player_id=PID)
        await asyncio.wait_for(cli.until_over(broken(), player), 1)


def test_inspect_shows_a_grudge_only_when_there_is_one():
    town = Town()
    town.add(Villager("v1", "Marta", "baker", "calm", "", [], {}))
    town.add(Villager("v2", "Bert", "miller", "sour", "the mayor cheated him", [], {}))

    assert not any(line.startswith("Grudge") for line in cli._inspect(town, "Marta"))
    assert "Grudge: the mayor cheated him" in cli._inspect(town, "Bert")


@pytest.mark.asyncio
async def test_trophies_lists_the_case(monkeypatch):
    town, feed, out = village()
    monkeypatch.setattr(cli, "_readline", scripted(["trophies\n", ""]))
    everyone = Players(feed)
    player = SimpleNamespace(id=PID, owner="o-1")
    await cli.repl(player, town, feed, players=everyone)
    assert out[-1] == "No trophies yet."
    everyone.cases["o-1"] = [Trophy("personal", ("a", "b", "c"), 3)]
    monkeypatch.setattr(cli, "_readline", scripted(["trophies\n", ""]))
    await cli.repl(player, town, feed, players=everyone)
    assert out[-1] == "Day 3: a; b; c"


@pytest.mark.asyncio
async def test_go_walks_and_prints_problems(monkeypatch):
    player, _, out = make_walker()
    monkeypatch.setattr(cli, "_readline", scripted(["go moon\n", "go mill\n", ""]))
    await cli.repl(player, player.town, player.feed)
    assert [line for line in out if "There's no moon" in line]
    assert player.place == "mill"
