import asyncio
from types import SimpleNamespace

import pytest

from examples.rumor_mill import cli
from examples.rumor_mill.cli import Command, parse_command
from examples.rumor_mill.config import Config
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import Refusal
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import Town


def test_parse_command_forms():
    assert parse_command("Marta: hello there") == Command("whisper", "Marta", "hello there")
    assert parse_command("whisper Marta: hi") == Command("whisper", "Marta", "hi")
    assert parse_command("Old Bert: hello") == Command("whisper", "Old Bert", "hello")
    assert parse_command("villagers") == Command("villagers")
    assert parse_command("inspect Old Bert") == Command("inspect", "Old Bert")
    assert parse_command("goals") == Command("goals")
    assert parse_command("help") == Command("help")
    assert parse_command("usage") == Command("usage")
    assert parse_command("quit") == Command("quit")
    assert parse_command("  quit  ") == Command("quit")
    assert parse_command("dance") == Command("unknown")
    assert parse_command("") == Command("unknown")
    assert parse_command(": nobody") == Command("unknown")


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
async def test_goals_also_prints_the_heat_label(monkeypatch):
    town, feed, out = village()
    suspicion = Suspicion(Config(), town)
    suspicion.bump("v1", 0.9, "whispered to them directly")
    monkeypatch.setattr(cli, "_readline", scripted(["goals\n", ""]))
    goal = Goal("g", "cat is a spy", "belief", ("cat spy",), "q", 0.5)
    await cli.repl(None, town, feed, [goal], {"g": 0.4}, suspicion=suspicion)
    assert out[-2:] == ["cat is a spy: 40%", "The village is hunting for you."]


@pytest.mark.asyncio
async def test_the_repl_ends_with_the_summary_when_the_game_is_over(monkeypatch):
    town, feed, out = village()
    game = Game(feed)
    monkeypatch.setattr(cli, "_readline", scripted([]))
    session = asyncio.create_task(cli.until_over(cli.repl(None, town, feed), game))
    await asyncio.sleep(0.01)
    game.lose(Summary("lost", [("cat is a spy", 40)], ["Marta heard it from you directly."]))
    await asyncio.wait_for(session, 1)
    assert out[-1].splitlines()[1:] == ["cat is a spy: 40%", "Marta heard it from you directly."]


@pytest.mark.asyncio
async def test_an_error_in_the_session_propagates_out_of_until_over():
    town, feed, _ = village()

    async def broken():
        raise RuntimeError("whisper failed")

    with pytest.raises(RuntimeError, match="whisper failed"):
        await asyncio.wait_for(cli.until_over(broken(), Game(feed)), 1)
