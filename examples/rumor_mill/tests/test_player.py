import asyncio

import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Player, echoes
from examples.rumor_mill.town import PLAYER_ID, Town


class FakeEngine:
    def __init__(self):
        self.delivered = []

    async def deliver(self, utterance, listener_ids):
        self.delivered.append((utterance, listener_ids))


def make(*names, shrug=0.05, cooldown=0.0):
    town, lines, engine = Town(), [], FakeEngine()
    lineage = Lineage()
    for i, name in enumerate(names, 1):
        town.add(Villager(f"v{i}", name, "baker", "calm", "none", [], {}), "tavern")
    feed = Feed(lineage, town, out=lines.append)
    player = Player(engine, town, lineage, Ids(), feed, Config(shrug_seconds=shrug, cooldown_seconds=cooldown))
    return player, town, lines, engine, lineage


@pytest.mark.asyncio
async def test_unknown_name_says_so_and_publishes_nothing():
    player, _, _, engine, _ = make("Marta")
    assert "Zed" in await player.whisper("Zed", "hello")
    assert engine.delivered == []


@pytest.mark.asyncio
async def test_ambiguous_name_lists_candidates():
    player, _, _, engine, _ = make("Bert Baker", "Bert Miller")
    problem = await player.whisper("Bert", "hello")
    assert engine.delivered == []
    assert "Bert Baker" in problem and "Bert Miller" in problem


@pytest.mark.asyncio
async def test_departed_villager_message():
    player, town, _, engine, _ = make("Marta")
    town.remove("v1")
    assert await player.whisper("Marta", "hello") == "Marta has left town."
    assert engine.delivered == []


@pytest.mark.asyncio
async def test_empty_and_overlong_whisper_rejected():
    player, _, _, engine, _ = make("Marta")
    assert await player.whisper("", "hello")
    assert await player.whisper("Marta", "   ")
    assert await player.whisper("Marta", "x" * (MAX_WHISPER_CHARS + 1))
    assert engine.delivered == []


@pytest.mark.asyncio
async def test_whisper_is_delivered_and_rooted_in_lineage():
    player, _, lines, engine, lineage = make("Marta", shrug=10)
    assert await player.whisper("Marta", "the miller waters his flour") is None
    (utterance, listeners), = engine.delivered
    assert listeners == ["v1"]
    assert (utterance.id, utterance.speaker, utterance.speaker_name) == ("w-1", PLAYER_ID, "The newcomer")
    assert (utterance.verb, utterance.place, utterance.parent) == ("whispered to you", "tavern", None)
    assert utterance.text == "the miller waters his flour"
    assert lineage.is_player_rooted("w-1")
    assert lines == []
    player.close()


@pytest.mark.asyncio
async def test_unheard_whisper_triggers_shrug_line():
    player, _, lines, _, _ = make("Marta")
    await player.whisper("Marta", "hello")
    await asyncio.sleep(0.2)
    assert lines == ["Marta shrugs it off."]
    shrug = player.feed.recent[-1]
    assert (shrug.kind, shrug.data["villager_id"], shrug.data["uid"]) == ("shrug", "v1", "w-1")


@pytest.mark.asyncio
async def test_heard_whisper_cancels_shrug():
    player, _, lines, _, _ = make("Marta")
    await player.whisper("Marta", "hello")
    player.note_heard("v1", [Match("utt:w-1", {"id": "w-1"})])
    await asyncio.sleep(0.2)
    assert lines == []


@pytest.mark.asyncio
async def test_shrug_waits_out_the_cooldown():
    player, _, lines, _, _ = make("Marta", shrug=0.1, cooldown=0.4)
    await player.whisper("Marta", "hello")
    await asyncio.sleep(0.3)
    assert lines == []
    for _ in range(40):
        if lines:
            break
        await asyncio.sleep(0.05)
    assert lines == ["Marta shrugs it off."]


@pytest.mark.asyncio
async def test_failed_whisper_says_so_and_leaves_no_shrug_task():
    player, _, lines, engine, _ = make("Marta", shrug=0.05)

    async def boom(utterance, listener_ids):
        raise RuntimeError("contex down")

    engine.deliver = boom
    assert await player.whisper("Marta", "hello") == "The whisper didn't carry; try again."
    await asyncio.sleep(0.2)
    assert lines == []
    assert player._shrugs == {}


@pytest.mark.asyncio
async def test_whisper_to_reaches_one_of_two_namesakes():
    player, _, _, engine, _ = make("Bert Cole", "Bert Cole", shrug=10)
    assert (await player.whisper("Bert Cole", "hi")).startswith("Which Bert Cole?")
    assert await player.whisper_to("v2", "hi") is None
    (_, listeners), = engine.delivered
    assert listeners == ["v2"]
    assert await player.whisper_to("v9", "hi") == "No one in town has that id."
    player.close()


NEEDS = [
    "I need someone to repair the water wheel before the spring thaw",
    "My daughter wants to apprentice as a weaver in the city",
    "The rats have gotten into my grain stores again",
]


class StubWriter:
    def __init__(self, *lines, gate=None):
        self.lines = list(lines)
        self.concerns = []
        self.gate = gate

    async def brush_off(self, villager, concern):
        self.concerns.append(concern)
        if self.gate:
            await self.gate.wait()
        line = self.lines.pop(0)
        if isinstance(line, Exception):
            raise line
        return line


def make_hinting(writer):
    player, town, lines, engine, lineage = make()
    town.add(Villager("v1", "Aldwin Miller", "miller", "tired", "the smith", list(NEEDS), {}), "mill")
    player.writer = writer
    return player, lines


async def shrug_once(player, lines):
    before = len(lines)
    await player.whisper_to("v1", "the tide tables were misprinted")
    for _ in range(100):
        if len(lines) > before:
            return lines[-1]
        await asyncio.sleep(0.01)
    return None


@pytest.mark.asyncio
async def test_shrug_brushes_off_with_unused_concerns_and_cycles():
    writer = StubWriter("Wheel's groaning.", "Girl wants the city.", "Rats again.", "Wheel again.")
    player, lines = make_hinting(writer)
    for _ in range(4):
        await shrug_once(player, lines)
    assert writer.concerns == [NEEDS[0], NEEDS[1], NEEDS[2], NEEDS[0]]
    assert lines[0] == 'Aldwin Miller brushes you off: "Wheel\'s groaning."'
    event = player.feed.recent[-1]
    assert (event.kind, event.data["hint"]) == ("shrug", "Wheel again.")
    assert not any(n in str(e.to_json()) for e in player.feed.recent for n in NEEDS)


@pytest.mark.asyncio
async def test_writer_failure_falls_back_to_a_plain_shrug():
    player, lines = make_hinting(StubWriter(RuntimeError("haiku down")))
    assert await shrug_once(player, lines) == "Aldwin Miller shrugs it off."
    assert player.feed.recent[-1].data["hint"] is None


@pytest.mark.asyncio
async def test_echoed_concern_is_retried_once_then_dropped():
    echo = "Leave me be, someone to repair the water wheel is all I want."
    writer = StubWriter(echo, "Fine, the wheel is creaking.")
    player, lines = make_hinting(writer)
    assert await shrug_once(player, lines) == 'Aldwin Miller brushes you off: "Fine, the wheel is creaking."'
    writer = StubWriter(echo, echo)
    player, lines = make_hinting(writer)
    assert await shrug_once(player, lines) == "Aldwin Miller shrugs it off."
    assert len(writer.concerns) == 2


@pytest.mark.asyncio
async def test_whisper_heard_during_generation_prints_nothing():
    gate = asyncio.Event()
    writer = StubWriter("Rats again.", gate=gate)
    player, lines = make_hinting(writer)
    await player.whisper_to("v1", "hello")
    for _ in range(100):
        if writer.concerns:
            break
        await asyncio.sleep(0.01)
    player.note_heard("v1", [Match("utt:w-1", {"id": "w-1"})])
    gate.set()
    await asyncio.sleep(0.1)
    assert lines == []


def test_echoes_needs_four_consecutive_words():
    assert echoes("The rats have gotten in, curse them", NEEDS[2])
    assert not echoes("Rats in the grain, again", NEEDS[2])
