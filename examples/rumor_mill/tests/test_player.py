import asyncio

import pytest

from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Refusal, echoes
from examples.rumor_mill.tests.helpers import make_player as make
from examples.rumor_mill.town import PLAYER_ID


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

    async def turn_away(self, villager):
        line = self.lines.pop(0)
        if isinstance(line, Exception):
            raise line
        return line

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


@pytest.mark.asyncio
async def test_no_brush_off_is_written_once_the_game_is_over():
    writer = StubWriter("Wheel's groaning.")
    player, lines = make_hinting(writer)
    player.game = Game(player.feed)
    await player.whisper_to("v1", "the tide tables were misprinted")
    player.game.lose(Summary("lost", [], []))
    await asyncio.sleep(0.15)
    assert writer.concerns == []


def test_echoes_needs_four_consecutive_words():
    assert echoes("The rats have gotten in, curse them", NEEDS[2])
    assert not echoes("Rats in the grain, again", NEEDS[2])


def suspect(player, vid="v1"):
    player.engine.suspicion.bump(vid, player.config.refuse_threshold, "test")


@pytest.mark.asyncio
async def test_whisper_raises_the_recipients_suspicion_even_when_it_shrugs():
    player, _, lines, _, _ = make("Marta")
    suspicion = player.engine.suspicion
    await player.whisper("Marta", "hello")
    assert suspicion.score("v1") == pytest.approx(player.config.suspicion_whisper_bump)
    for _ in range(100):
        if lines:
            break
        await asyncio.sleep(0.01)
    assert lines == ["Marta shrugs it off."]
    assert suspicion.score("v1") == pytest.approx(player.config.suspicion_whisper_bump)
    assert suspicion.top_evidence()[0].uid == "w-1"


@pytest.mark.asyncio
async def test_whisper_to_a_suspicious_villager_is_refused_and_not_delivered():
    player, _, _, engine, _ = make("Marta", shrug=0.05)
    suspect(player)
    assert await player.whisper("Marta", "hello") == Refusal("Marta turns away from you.")
    await asyncio.sleep(0.15)
    assert engine.delivered == []
    assert player._shrugs == {}
    assert [e.kind for e in player.feed.recent] == ["refusal"]


@pytest.mark.asyncio
async def test_refusal_bumps_suspicion_and_prints_a_line():
    player, _, lines, _, _ = make("Marta")
    player.writer = StubWriter("Not another word.")
    suspect(player)
    before = player.engine.suspicion.score("v1")
    await player.whisper("Marta", "hello")
    assert lines == ['Marta turns away from you: "Not another word."']
    after = player.engine.suspicion.score("v1")
    assert after == pytest.approx(min(1.0, before + player.config.suspicion_whisper_bump))
    assert "tried to whisper again" in {e.reason for e in player.engine.suspicion._evidence}


@pytest.mark.asyncio
async def test_refusal_falls_back_without_a_line_when_haiku_fails():
    player, _, lines, _, _ = make("Marta")
    player.writer = StubWriter(RuntimeError("haiku down"))
    suspect(player)
    assert isinstance(await player.whisper("Marta", "hello"), Refusal)
    assert lines == ["Marta turns away from you."]
    assert player.feed.recent[-1].data["line"] is None
    player.writer = None
    assert isinstance(await player.whisper("Marta", "hello"), Refusal)
    assert lines[-1] == "Marta turns away from you."


@pytest.mark.asyncio
async def test_refusal_event_carries_no_score():
    player, _, _, _, _ = make("Marta", "Bert")
    player.writer = StubWriter("Go away.")
    suspect(player)
    await player.whisper("Marta", "hello")
    event = player.feed.recent[-1].to_json()
    assert set(event) == {"id", "at", "kind", "name", "villager_id", "line"}
    assert not any(isinstance(v, float) for k, v in event.items() if k != "at")
    assert "Bert" not in str(event)


@pytest.mark.asyncio
async def test_failed_delivery_does_not_bump():
    player, _, _, engine, _ = make("Marta")

    async def boom(utterance, listener_ids):
        raise RuntimeError("contex down")

    engine.deliver = boom
    assert await player.whisper("Marta", "hello")
    assert player.engine.suspicion.score("v1") == 0.0
