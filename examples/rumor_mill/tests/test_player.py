import asyncio

import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Player
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

    class Say:
        say = staticmethod(lines.append)

    player = Player(engine, town, lineage, Ids(), Say, Config(shrug_seconds=shrug, cooldown_seconds=cooldown))
    return player, town, lines, engine, lineage


@pytest.mark.asyncio
async def test_unknown_name_says_so_and_publishes_nothing():
    player, _, lines, engine, _ = make("Marta")
    await player.whisper("Zed", "hello")
    assert engine.delivered == []
    assert "Zed" in lines[0]


@pytest.mark.asyncio
async def test_ambiguous_name_lists_candidates():
    player, _, lines, engine, _ = make("Bert Baker", "Bert Miller")
    await player.whisper("Bert", "hello")
    assert engine.delivered == []
    assert "Bert Baker" in lines[0] and "Bert Miller" in lines[0]


@pytest.mark.asyncio
async def test_departed_villager_message():
    player, town, lines, engine, _ = make("Marta")
    town.remove("v1")
    await player.whisper("Marta", "hello")
    assert engine.delivered == []
    assert lines == ["Marta has left town."]


@pytest.mark.asyncio
async def test_empty_and_overlong_whisper_rejected():
    player, _, lines, engine, _ = make("Marta")
    await player.whisper("", "hello")
    await player.whisper("Marta", "   ")
    await player.whisper("Marta", "x" * (MAX_WHISPER_CHARS + 1))
    assert engine.delivered == []
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_whisper_is_delivered_and_rooted_in_lineage():
    player, _, lines, engine, lineage = make("Marta", shrug=10)
    await player.whisper("Marta", "the miller waters his flour")
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
    await player.whisper("Marta", "hello")
    await asyncio.sleep(0.2)
    assert lines == ["The whisper didn't carry; try again."]
    assert player._shrugs == {}
