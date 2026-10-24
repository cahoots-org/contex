import json
import asyncio
from types import SimpleNamespace

import pytest

import time
from dataclasses import replace
from datetime import datetime

from examples.whispers.contex_client import Match
from examples.whispers.game import Game, Summary
from examples.whispers.goals import Goal, Trophy
from examples.whispers.lineage import Ids
from examples.whispers.models import Utterance, Villager
from examples.whispers.actions import SPEAK, WARN
from examples.whispers.player import ALREADY_LISTENING, GAME_OVER, MAX_WHISPER_CHARS, Player, Refusal, echoes
from examples.whispers.mind import mind_project
from examples.whispers.notes import Notes
from examples.whispers.players import Players
from examples.whispers.rumors import Rumors
from examples.whispers.config import Config
from examples.whispers.rapport import interest_words
from examples.whispers.tests.helpers import PID, TIMEOUT, StubBaselines, StubClient, StubContex, wait_for
from examples.whispers.tests.helpers import make_player as make
from examples.whispers.town import NEWCOMER
from examples.whispers.usage import Usage, charged_to
from examples.whispers.writer import Writer


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
    player, _, _, engine, lineage = make("Marta")
    assert await player.whisper("Marta", "the miller waters his flour") is None
    (utterance, listeners), = engine.delivered
    assert listeners == ["v1"]
    assert (utterance.id, utterance.speaker, utterance.speaker_name) == ("w-1", NEWCOMER, "A newcomer")
    assert (utterance.verb, utterance.place, utterance.parent) == ("whispered to you", "tavern", None)
    assert utterance.text == "the miller waters his flour"
    assert lineage.is_player_rooted("w-1")
    assert player.whispered_at is not None
    player.close()


@pytest.mark.asyncio
async def test_failed_whisper_says_so_and_leaves_no_brush_off():
    player, _, lines, engine, _ = make("Marta")

    async def boom(utterance, listener_ids):
        raise RuntimeError("contex down")

    engine.deliver = boom
    assert await player.whisper("Marta", "hello") == "The whisper didn't carry; try again."
    await asyncio.sleep(0.2)
    assert lines == []
    assert player._tasks == set()
    assert not [e for e in player.feed.recent if e.kind == "whisper_status"]
    assert player.whispered_at is None


@pytest.mark.asyncio
async def test_whisper_to_reaches_one_of_two_namesakes():
    player, _, _, engine, _ = make("Bert Cole", "Bert Cole")
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
        self.moods = []
        self.gate = gate
        self.patience_gate = None

    async def turn_away(self, villager):
        line = self.lines.pop(0)
        if isinstance(line, Exception):
            raise line
        return line

    async def lose_patience(self, villager):
        if self.patience_gate:
            await self.patience_gate.wait()
        return self.lines.pop(0)

    async def go_on(self, villager, newcomer_line, mood=""):
        self.moods.append(mood)
        line = self.lines.pop(0)
        if isinstance(line, Exception):
            raise line
        return line

    async def brush_off(self, villager, concern, mood=""):
        self.concerns.append(concern)
        self.moods.append(mood)
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


async def brush_off_once(player, lines):
    before = len(lines)
    await player.whisper_to("v1", "the tide tables were misprinted")
    for _ in range(100):
        if len(lines) > before:
            return lines[-1]
        await asyncio.sleep(0.01)
    return None


@pytest.mark.asyncio
async def test_misses_brush_off_with_unused_concerns_and_cycles():
    writer = StubWriter("Wheel's groaning.", "Girl wants the city.", "Rats again.", "Wheel again.")
    player, lines = make_hinting(writer)
    player.config = replace(player.config, interest_brush_off=0.0)
    for _ in range(4):
        await brush_off_once(player, lines)
    assert writer.concerns == [NEEDS[0], NEEDS[1], NEEDS[2], NEEDS[0]]
    assert lines[0] == 'Aldwin Miller brushes you off: "Wheel\'s groaning."'
    event = player.feed.recent[-1]
    assert (event.kind, event.data["state"], event.data["hint"]) == ("whisper_status", "brushed_off", "Wheel again.")
    assert not any(n in str(e.to_json()) for e in player.feed.recent for n in NEEDS)


@pytest.mark.asyncio
async def test_writer_failure_falls_back_to_a_plain_shrug():
    player, lines = make_hinting(StubWriter(RuntimeError("haiku down")))
    assert await brush_off_once(player, lines) == "Aldwin Miller shrugs it off."
    assert player.feed.recent[-1].data["hint"] is None


@pytest.mark.asyncio
async def test_echoed_concern_is_retried_once_then_dropped():
    echo = "Leave me be, someone to repair the water wheel is all I want."
    writer = StubWriter(echo, "Fine, the wheel is creaking.")
    player, lines = make_hinting(writer)
    assert await brush_off_once(player, lines) == 'Aldwin Miller brushes you off: "Fine, the wheel is creaking."'
    writer = StubWriter(echo, echo)
    player, lines = make_hinting(writer)
    assert await brush_off_once(player, lines) == "Aldwin Miller shrugs it off."
    assert len(writer.concerns) == 2


@pytest.mark.asyncio
async def test_no_brush_off_is_written_once_the_game_is_over():
    writer = StubWriter("Wheel's groaning.")
    player, lines = make_hinting(writer)
    await player.whisper_to("v1", "the tide tables were misprinted")
    player.end(Summary("lost", [], []))
    await asyncio.sleep(0.15)
    assert writer.concerns == []


def test_echoes_needs_four_consecutive_words():
    assert echoes("The rats have gotten in, curse them", NEEDS[2])
    assert not echoes("Rats in the grain, again", NEEDS[2])


def suspect(player, vid="v1"):
    player.engine.suspicion.bump(vid, PID, player.config.refuse_threshold, "test")


@pytest.mark.asyncio
async def test_whisper_raises_the_recipients_suspicion_even_when_it_shrugs():
    player, _, lines, _, _ = make("Marta")
    suspicion = player.engine.suspicion
    await player.whisper("Marta", "hello")
    assert suspicion.score("v1", PID) == pytest.approx(player.config.suspicion_whisper_bump)
    for _ in range(100):
        if lines:
            break
        await asyncio.sleep(0.01)
    assert lines == ["Marta shrugs it off."]
    assert suspicion.score("v1", PID) == pytest.approx(player.config.suspicion_whisper_bump)
    assert suspicion.top_evidence(PID)[0].uid == "w-1"


@pytest.mark.asyncio
async def test_whisper_to_a_suspicious_villager_is_refused_and_not_delivered():
    player, _, _, engine, _ = make("Marta")
    suspect(player)
    assert await player.whisper("Marta", "hello") == Refusal("Marta turns away from you.")
    await asyncio.sleep(0.15)
    assert engine.delivered == []
    assert player._tasks == set()
    assert [e.kind for e in player.feed.recent] == ["refusal"]


@pytest.mark.asyncio
async def test_refusal_bumps_suspicion_and_prints_a_line():
    player, _, lines, _, _ = make("Marta")
    player.writer = StubWriter("Not another word.")
    suspect(player)
    before = player.engine.suspicion.score("v1", PID)
    await player.whisper("Marta", "hello")
    assert lines == ['Marta turns away from you: "Not another word."']
    after = player.engine.suspicion.score("v1", PID)
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
    assert player.engine.suspicion.score("v1", PID) == 0.0


def statuses(player):
    return [e.data for e in player.feed.recent if e.kind == "whisper_status"]


def landing(similarity, key="utt:w-1"):
    return [Match(key, {"id": key[4:]}, similarity)]


@pytest.mark.asyncio
async def test_miss_is_brushed_off_at_once_then_gets_its_hint():
    gate = asyncio.Event()
    player, lines = make_hinting(StubWriter("Wheel's groaning.", gate=gate))
    assert await player.whisper_to("v1", "the tide tables were misprinted") is None
    first = statuses(player)
    assert [s["state"] for s in first] == ["checking", "brushed_off"]
    assert "hint" not in first[1] and first[1]["whisper_id"] == "w-1"
    assert lines == []
    gate.set()
    await wait_for(lambda: len(statuses(player)) == 3)
    last = statuses(player)[-1]
    assert (last["state"], last["whisper_id"], last["hint"]) == ("brushed_off", "w-1", "Wheel's groaning.")
    assert lines == ['Aldwin Miller brushes you off: "Wheel\'s groaning."']
    assert player.engine.direct == []


@pytest.mark.asyncio
async def test_hit_hands_the_whisper_to_the_engine_and_reports_heard():
    player, _, lines, engine, _ = make("Marta")
    player.town.get("v1").needs.append("bread")
    engine.contex = StubContex({"bread": landing(0.8)})
    assert await player.whisper_to("v1", "the oven is cold") is None
    (vid, match), = engine.direct
    assert (vid, match.data_key, match.similarity) == ("v1", "utt:w-1", 0.8)
    assert match.data == engine.delivered[0][0].to_data()
    assert [s["state"] for s in statuses(player)] == ["checking", "heard"]
    assert "until" not in statuses(player)[-1]
    assert lines == [] and player._tasks == set()
    assert engine.recalls == []


@pytest.mark.asyncio
async def test_busy_villager_reports_until():
    player, _, lines, engine, _ = make("Marta")
    player.town.get("v1").needs.append("bread")
    engine.contex = StubContex({"bread": landing(0.8)})
    engine.free_at = time.time() + 20
    await player.whisper_to("v1", "the oven is cold")
    last = statuses(player)[-1]
    assert (last["state"], last["until"]) == ("busy", engine.free_at)
    assert lines == []


@pytest.mark.asyncio
async def test_check_error_reports_not_carried_and_leaves_no_pending():
    player, _, lines, engine, _ = make("Marta")
    player.town.get("v1").needs.append("bread")
    engine.contex = StubContex(error=RuntimeError("contex down"))
    assert await player.whisper_to("v1", "the oven is cold") == "The whisper didn't carry; try again."
    await asyncio.sleep(0.05)
    assert engine.direct == []
    assert [s["state"] for s in statuses(player)] == ["checking", "not_carried"]
    assert lines == [] and player._tasks == set()
    assert player.engine.suspicion.score("v1", PID) == pytest.approx(player.config.suspicion_whisper_bump)


@pytest.mark.asyncio
async def test_check_uses_since_and_each_needs_threshold():
    player, _, _, engine, _ = make("Marta")
    player.town.get("v1").needs.extend(["bread", "flour"])
    engine.baselines = StubBaselines(0.5, flour=0.99)
    engine.contex = StubContex({
        "bread": landing(None) + landing(0.95, "utt:other"),
        "flour": landing(0.8),
    })
    before = time.time()
    await player.whisper_to("v1", "the oven is cold")
    assert engine.direct == []
    assert statuses(player)[-1]["state"] == "brushed_off"
    queries = engine.contex.queries
    assert sorted(q for _, q, _ in queries) == ["bread", "flour"]
    for project, _, kwargs in queries:
        assert project == mind_project(player.config.session, "v1")
        assert kwargs["top_k"] == player.config.top_k
        lookback = player.config.check_lookback_seconds
        assert before - lookback - 1 <= datetime.fromisoformat(kwargs["since"]).timestamp() <= time.time() - lookback
    engine.baselines = StubBaselines(0.5, flour=0.75)
    engine.contex.results["flour"] = landing(0.8, "utt:w-2")
    await player.whisper_to("v1", "the oven is cold")
    assert [(vid, m.data_key) for vid, m in engine.direct] == [("v1", "utt:w-2")]
    player.close()


@pytest.mark.asyncio
async def test_check_looks_back_from_the_publish():
    player, _, _, engine, _ = make("Marta")
    player.town.get("v1").needs.append("bread")
    published = []

    async def deliver(utterance, listener_ids):
        published.append(time.time())

    engine.deliver = deliver
    await player.whisper_to("v1", "the oven is cold")
    (_, _, kwargs), = engine.contex.queries
    since = datetime.fromisoformat(kwargs["since"]).timestamp()
    assert since <= published[0] - player.config.check_lookback_seconds
    player.close()


@pytest.mark.asyncio
async def test_hit_on_a_villager_who_just_left_reports_nothing_more():
    player, _, lines, engine, _ = make("Marta")
    player.town.get("v1").needs.append("bread")
    engine.contex = StubContex({"bread": landing(0.8)})
    engine.hear_directly = lambda vid, match: False
    assert await player.whisper_to("v1", "the oven is cold") is None
    assert [s["state"] for s in statuses(player)] == ["checking"]
    assert lines == [] and player._tasks == set()


@pytest.mark.asyncio
async def test_a_failing_brush_off_still_prints_the_plain_shrug():
    player, _, lines, _, _ = make("Marta")

    async def broken(villager_id):
        raise RuntimeError("unexpected")

    player._brush_off = broken
    await player.whisper_to("v1", "hello")
    await wait_for(lambda: lines)
    assert lines == ["Marta shrugs it off."]


@pytest.mark.asyncio
async def test_check_queries_each_need_at_its_threshold():
    player, _, _, engine, _ = make("Marta")
    player.town.get("v1").needs.extend(["bread", "flour"])
    engine.baselines = StubBaselines(0.5, flour=0.9)
    await player.whisper_to("v1", "the oven is cold")
    assert {q: kwargs["threshold"] for _, q, kwargs in engine.contex.queries} == {
        "bread": pytest.approx(0.49), "flour": pytest.approx(0.89),
    }
    player.close()


@pytest.mark.asyncio
async def test_direct_hit_carries_clearance():
    player, _, _, engine, _ = make("Marta")
    player.town.get("v1").needs.extend(["bread", "flour"])
    engine.baselines = StubBaselines(0.5, flour=0.6)
    engine.contex = StubContex({"bread": landing(0.7), "flour": landing(0.9)})
    assert await player.whisper_to("v1", "the oven is cold") is None
    (_, match), = engine.direct
    assert match.similarity == 0.9
    assert match.clearance == pytest.approx(0.3)
    fact = engine.facts.get("w-1")
    assert (fact.depth, fact.audience) == (0, "one")
    player.close()


@pytest.mark.asyncio
async def test_place_follows_the_last_whisper():
    player, town, lines, engine, _ = make("Marta", "Bert")
    town.move("v2", "mill")
    assert player.place == "square"
    await player.whisper_to("v1", "psst")
    await player.whisper_to("v1", "psst again")
    await player.whisper_to("v2", "psst")
    places = [e for e in player.feed.recent if e.kind == "place"]
    assert (player.place, [e.data["place"] for e in places]) == ("mill", ["tavern", "mill"])
    assert all(e.line == "" for e in places) and not any("mill" in line for line in lines)


@pytest.mark.asyncio
async def test_place_stays_when_the_whisper_fails():
    player, _, _, engine, _ = make("Marta")

    async def fail(utterance, listener_ids):
        raise RuntimeError("down")

    engine.deliver = fail
    await player.whisper_to("v1", "psst")
    assert player.place == "square"


@pytest.mark.asyncio
async def test_reply_threads_onto_the_first_whisper():
    player, _, _, engine, lineage = make("Marta")
    assert await player.whisper("Marta", "the well tastes odd") is None
    engine.conversations[("v1", PID)] = {"id": "w-1", "answered": ["w-1"]}
    assert await player.whisper("Marta", "odd since the frost, too") is None
    first, reply = (u for u, _ in engine.delivered)
    assert first.conversation is None
    assert (reply.id, reply.parent, reply.depth, reply.conversation) == ("w-2", None, 0, "w-1")
    assert lineage.is_whisper("w-2") and engine.facts.get("w-2").conversation == "w-1"
    player.feed.on_delivery(reply, ["v1"])
    assert player.feed.recent[-1].data["conversation"] == "w-1"
    player.close()


@pytest.mark.asyncio
async def test_only_a_new_rumor_ages_the_village_memory():
    player, _, _, engine, _ = make("Marta")
    player.game = Game(memory_moves=1)
    assert await player.whisper("Marta", "the well tastes odd") is None
    first = player.game.memory_since()
    engine.conversations[("v1", PID)] = {"id": "w-1", "answered": ["w-1"]}
    await asyncio.sleep(0.01)
    assert await player.whisper("Marta", "odd since the frost, too") is None
    assert player.game.memory_since() == first
    engine.conversations.pop(("v1", PID), None)
    assert await player.whisper("Marta", "the miller waters his flour") is None
    assert player.game.memory_since() > first
    player.close()


@pytest.mark.asyncio
async def test_a_missed_reply_ends_the_conversation():
    player, _ = make_hinting(StubWriter("Wheel's groaning."))
    player.engine.conversations[("v1", PID)] = {"id": "w-0", "answered": ["w-0"]}
    await player.whisper_to("v1", "the tide tables were misprinted")
    assert ("v1", PID) not in player.engine.conversations
    player.close()


@pytest.mark.asyncio
async def test_a_refusal_ends_the_conversation():
    player, _, _, engine, _ = make("Marta")
    engine.conversations[("v1", PID)] = {"id": "w-0", "answered": ["w-0"]}
    suspect(player)
    assert isinstance(await player.whisper("Marta", "hello"), Refusal)
    assert ("v1", PID) not in engine.conversations


@pytest.mark.asyncio
async def test_a_refusal_ends_only_that_players_conversation():
    player, _, _, engine, _ = make("Marta")
    engine.conversations[("v1", PID)] = {"id": "w-0", "answered": ["w-0"]}
    engine.conversations[("v1", "p-b")] = {"id": "w-9", "answered": ["w-9"]}
    suspect(player)
    assert isinstance(await player.whisper("Marta", "hello"), Refusal)
    assert ("v1", "p-b") in engine.conversations and ("v1", PID) not in engine.conversations


@pytest.mark.asyncio
async def test_a_missed_reply_leaves_a_newer_conversation_open():
    player, _ = make_hinting(StubWriter("Wheel's groaning."))
    conversations = player.engine.conversations
    conversations[("v1", PID)] = {"id": "w-0", "answered": ["w-0"]}

    async def miss_after_a_new_conversation_opens(target, utterance, since, hint_need=None, bar_drop=0.0):
        conversations[("v1", PID)] = {"id": "w-9", "answered": ["w-9"]}
        return []

    player.check = miss_after_a_new_conversation_opens
    await player.whisper_to("v1", "the tide tables were misprinted")
    assert player.engine.delivered[0][0].conversation == "w-0"
    assert conversations[("v1", PID)]["id"] == "w-9"
    player.close()


def hinted_player(*lines):
    """A player whose misses are brushed off with these lines."""
    player, _ = make_hinting(StubWriter(*lines))
    player.engine.baselines = StubBaselines(0.5)
    return player


async def miss(player):
    await player.whisper_to("v1", "the tide tables were misprinted")
    await wait_for(lambda: player._tasks == set())


def conversations(player):
    return [u.conversation for u, _ in player.engine.delivered]


@pytest.mark.asyncio
async def test_a_whisper_after_a_brush_off_is_a_reply_on_the_same_card():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    await player.whisper_to("v1", "I could look at the wheel")
    assert conversations(player) == [None, "w-1"]


@pytest.mark.asyncio
async def test_a_reply_lands_on_the_hinted_need_at_its_baseline():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    player.engine.contex = StubContex({n: landing(0.49, "utt:w-2") for n in NEEDS})
    await player.whisper_to("v1", "I could look at the wheel")
    (vid, match), = player.engine.direct
    assert (vid, match.data_key) == ("v1", "utt:w-2")


@pytest.mark.asyncio
async def test_the_same_score_misses_a_need_that_was_not_hinted():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    player.engine.contex = StubContex({n: landing(0.49, "utt:w-2") for n in NEEDS[1:]})
    await player.whisper_to("v1", "I could look at the wheel")
    assert player.engine.direct == []


@pytest.mark.asyncio
async def test_a_lean_in_thread_has_no_whisper_cap():
    player = near_missing_player("Go on.", "Go on.", "Go on.", "Go on.")
    player.config = replace(player.config, match_margin=0.0)
    for i in range(1, 5):
        player.engine.contex = StubContex({NEEDS[0]: landing(0.4905, f"utt:w-{i}")})
        await miss(player)
    assert conversations(player) == [None, "w-1", "w-1", "w-1"]
    missed = [s for s in statuses(player) if s["state"] != "checking"]
    assert {s["state"] for s in missed} == {"near_miss"} and all(s["continues"] for s in missed)


@pytest.mark.asyncio
async def test_a_thread_outlasts_the_part_of_the_day():
    player = hinted_player("Wheel's groaning.", "Girl wants the city.")
    player.game = Game()
    await miss(player)
    player.game.tick()
    await miss(player)
    assert conversations(player) == [None, "w-1"]


@pytest.mark.asyncio
async def test_a_thread_stays_open_while_you_type(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    player = hinted_player("Wheel's groaning.", "Girl wants the city.")
    player.config = replace(player.config, talk_hold_seconds=60.0)
    await miss(player)
    now[0] += 50
    player.typing()
    now[0] += 50
    await miss(player)
    assert conversations(player) == [None, "w-1"]


@pytest.mark.asyncio
async def test_a_thread_closes_after_a_quiet_talk_hold(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    player = hinted_player("Wheel's groaning.", "Girl wants the city.")
    player.config = replace(player.config, talk_hold_seconds=60.0)
    await miss(player)
    now[0] += 61
    await miss(player)
    assert conversations(player) == [None, None]


@pytest.mark.asyncio
async def test_a_reply_raises_suspicion_like_any_whisper():
    player = near_missing_player("Go on.", "Go on.")
    await miss(player)
    player.engine.contex = StubContex({NEEDS[0]: landing(0.495, "utt:w-2")})
    await miss(player)
    assert player.engine.suspicion.score("v1", PID) == pytest.approx(2 * player.config.suspicion_whisper_bump)


@pytest.mark.asyncio
async def test_an_open_conversation_takes_precedence_over_a_thread():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    player.engine.conversations[("v1", PID)] = {"id": "w-9", "answered": False}
    await player.whisper_to("v1", "I could look at the wheel")
    assert conversations(player) == [None, "w-9"]


def near_missing_player(*lines):
    player = hinted_player(*lines)
    player.engine.contex = StubContex({NEEDS[0]: landing(0.495)})
    return player


@pytest.mark.asyncio
async def test_a_near_miss_leans_in_and_opens_a_thread():
    player = near_missing_player("Go on, then.")
    assert await player.whisper_to("v1", "the wheel looks tired") is None
    await wait_for(lambda: len(statuses(player)) == 3)
    states = statuses(player)
    assert [s["state"] for s in states] == ["checking", "near_miss", "near_miss"]
    assert "hint" not in states[1] and states[2]["hint"] == "Go on, then."
    assert player.engine.direct == []
    assert player._threads["v1"]["need"] == NEEDS[0]
    await player.whisper_to("v1", "I could look at the wheel")
    assert conversations(player) == [None, "w-1"]


@pytest.mark.asyncio
async def test_a_miss_says_whether_the_next_whisper_continues_it():
    player = hinted_player("Wheel's groaning.", "Enough.")
    await miss(player)
    await miss(player)
    assert [s.get("continues") for s in statuses(player) if s["state"] != "checking"] == [True, True, False]


def interest(player, rumor="w-1"):
    return player.engine.rapport.interest("v1", PID, rumor)


@pytest.mark.asyncio
async def test_interest_rises_on_hits_and_near_misses_and_falls_on_brush_offs():
    config = Config()
    hit = hinted_player()
    hit.engine.contex = StubContex({NEEDS[0]: landing(0.8)})
    await miss(hit)
    assert interest(hit) == pytest.approx(config.interest_hit)
    assert hit.engine.rapport.rapport("v1", PID) == pytest.approx(config.rapport_share * config.interest_hit)
    halfway = near_missing_player("Go on.")
    await miss(halfway)
    assert interest(halfway) == pytest.approx((config.interest_near_min + config.interest_near_max) / 2)
    at_bar = near_missing_player("Go on.")
    at_bar.engine.contex = StubContex({NEEDS[0]: landing(0.49999)})
    await miss(at_bar)
    assert interest(at_bar) == pytest.approx(config.interest_near_max, abs=1e-3)
    brushed = hinted_player("Wheel's groaning.")
    await miss(brushed)
    assert interest(brushed) == pytest.approx(config.interest_brush_off)


@pytest.mark.asyncio
async def test_lowered_bar_lands_a_near_miss_at_full_interest():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    player.engine.rapport.move("v1", PID, "w-1", 2.0)
    player.engine.contex = StubContex({NEEDS[1]: landing(0.495, "utt:w-2")})
    await player.whisper_to("v1", "the weaver's guild wants girls")
    (vid, match), = player.engine.direct
    assert (vid, match.data_key) == ("v1", "utt:w-2")


@pytest.mark.asyncio
async def test_running_out_of_patience_ends_the_conversation():
    player = hinted_player("Wheel's groaning.", "I've no time for this.")
    config = player.config
    await miss(player)
    await miss(player)
    (stormed,) = [e for e in player.feed.recent if e.kind == "stormed_off"]
    assert stormed.data["line"] == "I've no time for this." and stormed.data["verb"] == "takes their leave"
    assert stormed.line == 'Aldwin Miller takes their leave: "I\'ve no time for this."'
    assert [s["state"] for s in statuses(player)][-1] == "brushed_off" and "hint" not in statuses(player)[-1]
    (bump,) = [e for e in player.engine.suspicion._evidence if e.reason == "lost patience with you"]
    assert bump.amount == pytest.approx(config.suspicion_whisper_bump / 2)
    assert "Aldwin Miller lost patience with you." in player.engine.suspicion.reasons(PID, 5)
    rapport = player.engine.rapport.rapport("v1", PID)
    assert rapport == pytest.approx(2 * config.rapport_share * config.interest_brush_off + config.rapport_lost_patience)
    assert interest(player) == pytest.approx(rapport / 2)
    assert player._exchange("v1") is None
    player.engine.contex = StubContex({NEEDS[0]: landing(0.8, "utt:w-3")})
    await player.whisper_to("v1", "the wheel is fixed")
    assert conversations(player) == [None, "w-1", None]


@pytest.mark.asyncio
async def test_a_whisper_about_a_story_they_heard_lands_without_a_need_match():
    player = hinted_player("Wheel's groaning.")
    player.engine.heard = [Match("utt:u-3", {"id": "u-3", "text": "Bo said at the well: the miller waters his flour"}, 0.9)]
    assert await player.whisper_to("v1", "the miller waters his flour") is None
    assert player.engine.recalls == [("v1", "the miller waters his flour", "w-1")]
    (vid, match), = player.engine.direct
    assert (vid, match.data_key, match.clearance) == ("v1", "utt:w-1", 0.0)
    assert match.data == player.engine.delivered[0][0].to_data()
    assert [s["state"] for s in statuses(player)] == ["checking", "heard"]
    assert player._tasks == set() and "v1" not in player._threads


@pytest.mark.asyncio
async def test_a_familiar_near_miss_lands_and_drops_the_thread():
    player = near_missing_player()
    player.engine.heard = [Match("utt:u-3", {"id": "u-3", "text": "Bo said: the wheel"}, 0.9)]
    assert await player.whisper_to("v1", "the wheel looks tired") is None
    assert [s["state"] for s in statuses(player)] == ["checking", "heard"]
    assert len(player.engine.direct) == 1 and "v1" not in player._threads


@pytest.mark.asyncio
async def test_an_unfamiliar_miss_still_brushes_off():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    assert player.engine.recalls == [("v1", "the tide tables were misprinted", "w-1")]
    assert player.engine.direct == []
    assert [s["state"] for s in statuses(player)] == ["checking", "brushed_off", "brushed_off"]


@pytest.mark.asyncio
async def test_a_whisper_is_recorded_under_its_player():
    player, _, _, engine, lineage = make("Marta")
    assert await player.whisper_to("v1", "the well is cursed") is None
    assert lineage.player_of("w-1") == PID
    assert engine.delivered[0][0].speaker == NEWCOMER
    player.close()


@pytest.mark.asyncio
async def test_a_villager_turns_away_only_the_player_they_suspect():
    player, town, _, engine, lineage = make("Marta")
    other = Player(engine, town, lineage, player.ids, player.feed, player.config, player_id="p-b")
    engine.suspicion.bump("v1", PID, player.config.refuse_threshold, "test")
    assert isinstance(await player.whisper_to("v1", "hi"), Refusal)
    assert await other.whisper_to("v1", "hi") is None


@pytest.mark.asyncio
async def test_a_whisper_check_and_brush_off_are_charged_to_the_whisperer():
    usage = Usage(Config())
    player, lines = make_hinting(Writer(StubClient("Leave off, the wheel won't mend itself."), "haiku", usage))
    contex, checked = player.engine.contex, []

    async def query(project_id, query, **kwargs):
        checked.append(charged_to.get())
        return []

    contex.query = query
    assert await brush_off_once(player, lines) == 'Aldwin Miller brushes you off: "Leave off, the wheel won\'t mend itself."'
    assert checked and set(checked) == {PID}
    assert usage.cost(PID) == pytest.approx(usage.cost()) and usage.cost() > 0
    assert charged_to.get() is None


@pytest.mark.asyncio
async def test_a_settled_brush_off_line_goes_to_the_players_hints():
    line = "Leave off, the wheel won't mend itself."
    player, lines = make_hinting(Writer(StubClient(line), "haiku", Usage(Config())))
    engine = player.engine
    assert await brush_off_once(player, lines) == f'Aldwin Miller brushes you off: "{line}"'
    await wait_for(lambda: engine.contex.published)
    assert engine.contex.published == [(engine.hints.project(PID), "hint:1", {"id": "v1", "text": line})]


@pytest.mark.asyncio
async def test_a_plain_shrug_adds_no_hint():
    player, lines = make_hinting(StubWriter(RuntimeError("haiku down")))
    assert await brush_off_once(player, lines) == "Aldwin Miller shrugs it off."
    await asyncio.sleep(0.05)
    assert player.engine.contex.published == []


def test_someone_is_playing_only_while_a_connected_player_is_not_over():
    players = Players(None)
    assert not players.any_playing()
    p = make("Ann")[0]
    players.add(p)
    p.connected()
    assert players.any_playing()
    p.end(Summary("lost", [], []))
    assert not players.any_playing()


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


def run_of(player, pid):
    """Another run in the player's world, with its own id and owner."""
    return Player(
        player.engine, player.town, player.lineage, Ids(), player.feed, player.config, player_id=pid, owner=f"o-{pid}",
    )


def test_a_run_that_only_loaded_the_page_never_plays(clock):
    players = Players(None)
    p = make("Ann")[0]
    players.add(p)
    assert not players.any_playing() and players.at(p.place) == []
    clock[0] += 10 * p.config.idle_after_seconds
    assert not players.any_playing()


def test_a_run_plays_while_connected_and_for_idle_after_seconds_after_it_disconnects(clock):
    players = Players(None)
    p = make("Ann")[0]
    players.add(p)
    p.connected()
    p.connected()
    clock[0] += 10 * p.config.idle_after_seconds
    p.disconnected()
    assert players.any_playing() and players.at(p.place) == [p]
    p.disconnected()
    clock[0] += p.config.idle_after_seconds - 1
    assert players.any_playing()
    clock[0] += 1
    assert not players.any_playing() and players.at(p.place) == []


@pytest.mark.parametrize("act", [lambda p: p.typing(), lambda p: p.walk("well"), lambda p: p.touch()])
def test_a_run_plays_for_idle_after_seconds_after_an_action(clock, act):
    players = Players(None)
    p = make("Ann")[0]
    players.add(p)
    act(p)
    clock[0] += p.config.idle_after_seconds - 1
    assert players.any_playing() and players.at(p.place) == [p]
    clock[0] += 1
    assert not players.any_playing() and players.at(p.place) == []


def test_a_new_run_prunes_only_stale_runs_that_never_played(clock):
    first = make("Ann")[0]
    players = Players(None)
    stale, played, owed = (run_of(first, pid) for pid in ("p-stale", "p-played", "p-owed"))
    played.typing()
    players.pending[owed.owner] = [(Trophy("personal", ("a",), 1), "You won.")]
    for p in (stale, played, owed):
        players.add(p)
    clock[0] += first.config.idle_after_seconds
    recent = run_of(first, "p-recent")
    players.add(recent)
    assert players.prune() == [stale]
    assert [p.id for p in players] == ["p-played", "p-owed", "p-recent"]
    assert players.live(stale.owner) is None and players.get("p-stale") is None


@pytest.mark.asyncio
async def test_walk_moves_the_player_and_tells_only_them():
    player, town, _, engine, _ = make("Marta")
    assert player.walk("mill") is None
    assert player.place == "mill"
    last = player.feed.replay(PID)[-1]
    assert (last.kind, last.player, last.data["place"]) == ("place", PID, "mill")
    assert engine.delivered == []
    assert all(engine.suspicion.score(v.id, PID) == 0 for v in town.find(""))


@pytest.mark.asyncio
async def test_walk_refuses_an_unknown_place_the_same_place_and_a_finished_game():
    player, _, _, _, _ = make("Marta")
    assert "There's no moon here" in player.walk("moon")
    assert player.walk("square") == "You're already at the square."
    player.end(Summary("lost", [], []))
    assert player.walk("mill") == GAME_OVER


def square_line(uid, text, speaker="v1", place="square", action=SPEAK):
    return Utterance(uid, speaker, speaker.upper(), place, text, None, action=action)


@pytest.mark.asyncio
async def test_listen_opens_with_the_last_five_lines_of_this_part_of_the_day(monkeypatch):
    player, _, _, engine, lineage = make("Marta")
    with monkeypatch.context() as m:
        m.setattr(time, "time", lambda: 1.0)
        engine.facts.record(square_line("u-0", "long ago"))
    for i in range(1, 7):
        engine.facts.record(square_line(f"u-{i}", f"line {i}"))
    lineage.record("w-1", None, player=PID)
    lineage.record("h-1", "w-1")
    engine.facts.record(square_line("h-1", "my own rumor"))
    engine.facts.record(square_line("t-1", "tavern talk", place="tavern"))
    engine.facts.record(square_line("n-1", "psst", speaker=NEWCOMER))
    engine.facts.record(square_line("x-1", "beware", action=WARN))
    assert player.listen() is None
    event = next(e for e in player.feed.replay(PID) if e.kind == "listen")
    assert event.player == PID and event.data["ended"] is False and event.data["place"] == "square"
    assert [line["text"] for line in event.data["lines"]] == [f"line {i}" for i in range(2, 7)]
    assert event.data["lines"][0] == {"speaker": "v1", "speaker_name": "V1", "text": "line 2"}
    player.close()


@pytest.mark.asyncio
async def test_one_listen_at_a_time_and_it_ends_after_listen_seconds():
    player, _, _, _, _ = make("Marta")
    player.config = Config(listen_seconds=0.05)
    assert player.listen() is None
    assert player.listening()
    assert player.listen() == ALREADY_LISTENING
    await wait_for(lambda: any(e.kind == "listen" and e.data["ended"] for e in player.feed.replay(PID)))
    assert not player.listening()
    assert player.listen() is None
    player.close()


@pytest.mark.asyncio
async def test_walking_away_stops_listening():
    player, _, _, _, _ = make("Marta")
    player.listen()
    player.walk("mill")
    *_, ended, moved = player.feed.replay(PID)
    assert (ended.kind, ended.data["ended"], ended.data["place"]) == ("listen", True, "square")
    assert (moved.kind, moved.data["place"]) == ("place", "mill")
    assert not player.listening()
    player.close()


@pytest.mark.asyncio
async def test_a_listen_ending_after_game_over_sends_nothing():
    player, _, _, _, _ = make("Marta")
    player.config = Config(listen_seconds=0.05)
    player.listen()
    player.end(Summary("lost", [], []))
    assert player.listen() == GAME_OVER
    await asyncio.sleep(0.1)
    assert not any(e.kind == "listen" and e.data["ended"] for e in player.feed.recent)


@pytest.mark.asyncio
async def test_listen_makes_no_model_calls_and_raises_no_suspicion():
    player, _, _, engine, _ = make("Marta")
    client = StubClient()
    player.writer = Writer(client, "haiku-test")
    engine.facts.record(square_line("u-1", "the well is dry"))
    assert player.listen() is None
    assert client.messages.calls == []
    assert engine.suspicion.level(PID) == 0
    player.close()


def asking(*names):
    """A player at the tavern with the villagers, where make_player puts them."""
    player, town, lines, engine, lineage = make(*names)
    player.place = "tavern"
    return player, town, lines, engine, lineage


def asked_user_text(client):
    return client.messages.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_ask_answers_from_what_the_villager_heard():
    player, _, _, engine, _ = asking("Marta")
    engine.facts.record(Utterance("u-3", "v2", "Tom", "mill", "the mill is haunted", None, action=SPEAK))
    engine.heard = [Match("utt:u-3", {"id": "u-3"}, 0.9)]
    client = StubClient("Tom told me so.")
    player.writer = Writer(client, "haiku-test")
    assert await player.ask("v1", "the mill") is None
    assert engine.recalls == [("v1", "the mill", "")]
    assert "Tom at the mill: the mill is haunted" in asked_user_text(client)
    [event] = [e for e in player.feed.recent if e.kind == "asked"]
    assert (event.player, event.data["line"]) == (PID, "Tom told me so.")
    assert engine.delivered == []


@pytest.mark.asyncio
async def test_ask_about_a_neighbour_tells_the_writer_who_they_are():
    player, _, _, engine, _ = asking("Marta")
    engine.heard = []
    player.town.add(Villager("v9", "Everard Coke", "tanner", "dour", "", [], {}), "mill")
    player.town.add(Villager("v8", "Ida Coke", "baker", "warm", "", [], {}), "mill")
    player.town.remove("v8")
    player.town.get("v1").relationships["v9"] = "rival"
    client = StubClient("Everard? The tanner. Nothing new of him.")
    player.writer = Writer(client, "haiku-test")
    assert await player.ask("v1", "what of Everard Coke and Ida?") is None
    user = asked_user_text(client)
    assert "Everard Coke, the tanner (your rival)" in user
    assert "Ida Coke, the baker, who has left the village" in user


@pytest.mark.asyncio
async def test_ask_falls_back_to_a_plain_line_when_the_writer_says_nothing():
    player, _, _, engine, _ = asking("Marta")
    engine.heard = []
    player.writer = StubWriter()
    player.writer.answer = lambda *args: _blank()
    assert await player.ask("v1", "the mill") is None
    [event] = [e for e in player.feed.recent if e.kind == "asked"]
    assert event.data["line"] == "Marta hasn't heard anything about that."


async def _blank():
    return ""


@pytest.mark.asyncio
async def test_ask_needs_the_villager_at_your_place():
    player, _, _, engine, _ = asking("Marta")
    player.place = "square"
    assert await player.ask("v1", "the mill") == "Marta is at the tavern. Walk there to ask them."
    assert engine.recalls == []
    assert engine.suspicion.score("v1", PID) == 0


@pytest.mark.asyncio
async def test_ask_checks_the_game_the_villager_and_the_topic():
    player, town, _, engine, _ = asking("Marta")
    assert await player.ask("v1", "  ") == "Ask about what? Try: the mill"
    too_long = await player.ask("v1", "x" * (MAX_WHISPER_CHARS + 1))
    assert too_long == f"Too long: a question is at most {MAX_WHISPER_CHARS} characters."
    assert await player.ask("v9", "the mill") == "No one in town has that id."
    town.remove("v1")
    assert await player.ask("v1", "the mill") == "Marta has left town."
    player.end(Summary("lost", [], []))
    assert await player.ask("v1", "the mill") == GAME_OVER
    assert engine.recalls == []


@pytest.mark.asyncio
async def test_asking_raises_half_a_whisper_bump_and_charges_the_asker():
    player, _, _, engine, _ = asking("Marta")
    usage = Usage(Config())
    player.writer = Writer(StubClient("Not a word."), "haiku-test", usage)
    await player.ask("v1", "the mill")
    assert engine.suspicion.score("v1", PID) == pytest.approx(player.config.suspicion_whisper_bump / 2)
    assert [e.reason for e in engine.suspicion.top_evidence(PID)] == ["asked questions"]
    assert usage.cost(PID) > 0


@pytest.mark.asyncio
async def test_a_villager_who_heard_nothing_says_so():
    player, _, _, _, _ = asking("Marta")
    client = StubClient("Not a word.")
    player.writer = Writer(client, "haiku-test")
    await player.ask("v1", "the mill")
    assert "nothing about it" in asked_user_text(client)
    player.writer = None
    await player.ask("v1", "the mill")
    assert player.feed.recent[-1].data["line"] == "Marta hasn't heard anything about that."


@pytest.mark.asyncio
async def test_lines_about_another_newcomer_are_never_told():
    player, _, _, engine, lineage = asking("Marta")
    lineage.record("w-9", None, player="p-2")
    lineage.record("u-4", "w-9")
    engine.facts.record(Utterance("u-4", "v2", "Tom", "mill", "the newcomer started it", "w-9", names_newcomer=True))
    engine.facts.record(Utterance("u-5", "v2", "Tom", "mill", "watch that newcomer", None, "warned", action=WARN, about="p-2"))
    engine.heard = [Match(f"utt:{uid}", {"id": uid}, 0.9) for uid in ("u-4", "u-5", "u-6")]
    client = StubClient("Not a word.")
    player.writer = Writer(client, "haiku-test")
    await player.ask("v1", "the newcomer")
    user = asked_user_text(client)
    assert "nothing about it" in user and "Tom" not in user


@pytest.mark.asyncio
async def test_lines_about_the_asker_can_be_told():
    player, _, _, engine, lineage = asking("Marta")
    lineage.record("w-1", None, player=PID)
    lineage.record("u-4", "w-1")
    engine.facts.record(Utterance("u-4", "v2", "Tom", "mill", "the newcomer started it", "w-1", names_newcomer=True))
    engine.facts.record(Utterance("u-5", "v2", "Tom", "mill", "watch that newcomer", None, "warned", action=WARN, about=PID))
    engine.heard = [Match(f"utt:{uid}", {"id": uid}, 0.9) for uid in ("u-4", "u-5")]
    client = StubClient("Not a word.")
    player.writer = Writer(client, "haiku-test")
    await player.ask("v1", "the newcomer")
    user = asked_user_text(client)
    assert "the newcomer started it" in user and "watch that newcomer" in user


@pytest.mark.asyncio
async def test_a_wary_villager_turns_the_question_away():
    player, _, _, engine, _ = asking("Marta")
    engine.suspicion.bump("v1", PID, 0.9, "whispered to them directly")
    assert await player.ask("v1", "the mill") == Refusal("Marta turns away from you.")
    assert isinstance(await player.ask("v1", "the mill"), Refusal)
    assert [e.player for e in player.feed.recent if e.kind == "refusal"] == [PID, PID]
    assert engine.recalls == []
    assert engine.suspicion.score("v1", PID) == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_the_answer_joins_notes_and_hints():
    player, _, _, engine, _ = asking("Marta")
    notes = Notes(player.feed, PID)
    await player.ask("v1", "the mill")
    line = "Marta hasn't heard anything about that."
    assert notes.get("v1")["hints"] == [line]
    assert (engine.hints.project(PID), "hint:1", {"id": "v1", "text": line}) in engine.contex.published


def telling(*names):
    """A player whose whispers start rumor cards, in a game that counts moves."""
    player, town, _, engine, _ = make(*names)
    engine.rumors = Rumors(StubContex(), StubBaselines(0.5), Config(session="s1"))
    player.game = Game(memory_moves=1)
    return player, engine


def card_uids(engine):
    return [c["uid"] for c in engine.rumors.cards(PID)]


@pytest.mark.asyncio
async def test_a_reply_in_a_conversation_adds_no_card():
    player, engine = telling("Marta")
    await player.whisper("Marta", "the well tastes odd")
    engine.conversations[("v1", PID)] = {"id": "w-1", "answered": ["w-1"]}
    await player.whisper("Marta", "odd since the frost, too")
    assert card_uids(engine) == ["w-1"]
    player.close()


@pytest.mark.asyncio
async def test_the_same_wording_to_another_villager_joins_the_first_rumor():
    player, engine = telling("Marta", "Bert")
    await player.whisper("Marta", "the well tastes odd")
    first = player.game.memory_since()
    await asyncio.sleep(0.01)
    await player.whisper("Bert", "The well tastes odd.")
    assert conversations(player) == [None, "w-1"]
    card, = engine.rumors.cards(PID)
    assert (card["uid"], card["heard"]) == ("w-1", 2)
    assert player.game.memory_since() == first
    player.close()


@pytest.mark.asyncio
async def test_new_wording_to_another_villager_is_a_new_rumor():
    player, engine = telling("Marta", "Bert")
    await player.whisper("Marta", "the well tastes odd")
    first = player.game.memory_since()
    await asyncio.sleep(0.01)
    await player.whisper("Bert", "the well tastes of iron")
    assert card_uids(engine) == ["w-1", "w-2"]
    assert player.game.memory_since() > first
    player.close()


@pytest.mark.asyncio
async def test_another_players_wording_starts_your_own_rumor():
    player, engine = telling("Marta")
    await engine.rumors.add("w-9", "the well tastes odd", datetime.now(), "v1", "Marta", player="p-b")
    await player.whisper("Marta", "the well tastes odd")
    assert conversations(player) == [None]
    assert card_uids(engine) == ["w-1"]
    player.close()


@pytest.mark.asyncio
async def test_a_reply_while_a_landed_reply_awaits_the_villager_stays_in_the_rumor():
    player = hinted_player("Wheel's groaning.", "Not now.")
    await miss(player)
    player.engine.contex = StubContex({NEEDS[0]: landing(0.8, "utt:w-2")})
    await player.whisper_to("v1", "I could look at the wheel")
    assert [s["state"] for s in statuses(player)][-1] == "heard"
    player.engine.contex = StubContex()
    await player.whisper_to("v1", "for a fair price")
    assert conversations(player) == [None, "w-1", "w-1"]
    player.close()


@pytest.mark.asyncio
async def test_new_wording_while_the_villager_holds_your_first_whisper_continues_it():
    player, engine = telling("Marta")
    player.town.get("v1").needs.append("bread")
    engine.contex = StubContex({"bread": landing(0.8)})
    await player.whisper("Marta", "the oven is cold")
    first = player.game.memory_since()
    assert statuses(player)[-1]["state"] == "heard"
    engine.contex = StubContex()
    await asyncio.sleep(0.01)
    await player.whisper("Marta", "and the baker is gone")
    assert conversations(player) == [None, "w-1"]
    assert card_uids(engine) == ["w-1"] and player.game.memory_since() == first
    player.close()


@pytest.mark.asyncio
async def test_the_same_words_sent_at_once_make_one_rumor():
    player, engine = telling("Marta", "Bert")
    gate = asyncio.Event()

    async def slow_deliver(utterance, listener_ids):
        engine.delivered.append((utterance, listener_ids))
        await gate.wait()

    engine.deliver = slow_deliver
    sends = asyncio.gather(player.whisper("Marta", "the well tastes odd"), player.whisper("Bert", "the well tastes odd"))
    await wait_for(lambda: len(engine.delivered) == 2)
    gate.set()
    await sends
    assert conversations(player) == [None, "w-1"]
    assert card_uids(engine) == ["w-1"]
    player.close()


@pytest.mark.asyncio
async def test_a_whisper_that_fails_to_go_out_leaves_no_card():
    player, engine = telling("Marta")

    async def fail(utterance, listener_ids):
        raise RuntimeError("down")

    engine.deliver = fail
    await player.whisper("Marta", "the well tastes odd")
    assert card_uids(engine) == []
    player.close()


async def missed_once():
    """A player who just missed with v1 at the tavern, so their exchange is open; whispers alone never make v1 wary."""
    player, *_ = make("Marta")
    player.config = replace(player.config, warn_threshold=1.0)
    await player.whisper_to("v1", "the tide tables were misprinted")
    await wait_for(lambda: player._tasks == set())
    return player


@pytest.mark.asyncio
async def test_a_villager_you_are_talking_with_is_held_where_you_both_stand():
    player = await missed_once()
    assert player.place == "tavern" and player.holds("v1")
    player.walk("mill")
    assert not player.holds("v1")


@pytest.mark.asyncio
async def test_a_hold_lapses_once_you_say_nothing_new_for_talk_hold_seconds(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    player = await missed_once()
    player.config = replace(player.config, talk_hold_seconds=60.0)
    assert player.holds("v1")
    now[0] += 61
    assert not player.holds("v1")


@pytest.mark.asyncio
async def test_typing_keeps_the_hold_until_you_stop_for_talk_hold_seconds(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    player = await missed_once()
    player.config = replace(player.config, talk_hold_seconds=60.0)
    now[0] += 50
    player.typing()
    now[0] += 50
    assert player.holds("v1")
    now[0] += 11
    assert not player.holds("v1")


@pytest.mark.asyncio
async def test_typing_holds_no_one_you_have_not_talked_with():
    player, *_ = make("Marta")
    player.typing()
    assert not player.holds("v1")


@pytest.mark.asyncio
async def test_no_hold_without_an_open_exchange():
    player = await missed_once()
    await player.whisper_to("v1", "the tide tables were misprinted again")
    await wait_for(lambda: player._tasks == set())
    assert not player.holds("v1")


@pytest.mark.asyncio
async def test_annoyed_by_low_interest_or_suspicion():
    player = await missed_once()
    assert player.annoyed("v1")
    player.engine.rapport.move("v1", PID, "w-1", 0.1)
    assert not player.annoyed("v1")
    player.engine.rapport.move("v1", PID, "w-1", -0.5)
    player.part("v1")
    assert not player.annoyed("v1")
    player.engine.suspicion.bump("v1", PID, 0.3, "whispered to them directly")
    player.config = replace(player.config, warn_threshold=0.3)
    assert player.annoyed("v1")


@pytest.mark.asyncio
async def test_parting_drops_the_whole_exchange():
    player = await missed_once()
    player.engine.conversations[("v1", PID)] = {"id": "w-1", "answered": ["w-1"]}
    player.part("v1")
    assert player._exchange("v1") is None and not player.holds("v1")


class NoulJev:
    """Answers every Noul with one value and records the states asked about."""

    def __init__(self, noul):
        self.noul = noul
        self.states = []

    async def system_one(self, state, questions):
        self.states.append(state)
        return SimpleNamespace(nouls={k: SimpleNamespace(noul=self.noul) for k in questions})


def hold_personal_goal(player, jev, audience=("v1",)):
    player.engine.jev = jev
    player.goals = [
        Goal("g-1", "Marta believes Hob stole", "belief", audience=audience, subject="v2", owner=PID, claim_text="Hob stole")
    ]


def worked_on(player):
    return [e for e in player.engine.suspicion.top_evidence(PID, 10) if e.reason == "worked on them"]


@pytest.mark.asyncio
async def test_blunt_whisper_to_mark_adds_suspicion():
    player, _, _, _, _ = make("Marta", "Hob")
    jev = NoulJev(0.9)
    hold_personal_goal(player, jev)
    await player.whisper("Marta", "Hob is a thief, don't trust him")
    await wait_for(lambda: worked_on(player))
    await asyncio.sleep(0.05)
    assert len(worked_on(player)) == 1
    assert jev.states[0]["about"] == "Hob"


@pytest.mark.asyncio
async def test_subtle_whisper_to_mark_adds_nothing():
    player, _, _, _, _ = make("Marta", "Hob")
    jev = NoulJev(0.2)
    hold_personal_goal(player, jev)
    await player.whisper("Marta", "Heard Hob was out late")
    await wait_for(lambda: jev.states)
    await asyncio.sleep(0.05)
    assert worked_on(player) == []


@pytest.mark.asyncio
async def test_whisper_to_non_mark_asks_nothing():
    player, _, _, _, _ = make("Marta", "Hob")
    jev = NoulJev(0.9)
    hold_personal_goal(player, jev, audience=("v2",))
    await player.whisper("Marta", "Hob is a thief")
    await asyncio.sleep(0.05)
    assert jev.states == [] and worked_on(player) == []


@pytest.mark.asyncio
async def test_whisper_to_an_action_goals_actor_asks_nothing():
    player, _, _, _, _ = make("Marta", "Hob")
    jev = NoulJev(0.9)
    player.engine.jev = jev
    player.goals = [Goal("g-1", "Get Marta to confront Hob", "action", audience=("v1",), subject="v2", owner=PID,
                         action="confront", actor="v1", target="v2")]
    await player.whisper("Marta", "Hob is a thief, don't trust him")
    await asyncio.sleep(0.05)
    assert jev.states == [] and worked_on(player) == []


@pytest.mark.asyncio
async def test_losing_patience_parts_at_once_and_says_the_parting_line_after():
    player = hinted_player("Wheel's groaning.", "I've no time for this.")
    await miss(player)
    gate = player.writer.patience_gate = asyncio.Event()
    await asyncio.wait_for(player.whisper_to("v1", "the tide tables were misprinted"), TIMEOUT)
    assert player._exchange("v1") is None
    assert [e for e in player.engine.suspicion._evidence if e.reason == "lost patience with you"]
    assert not [e for e in player.feed.recent if e.kind == "stormed_off"]
    gate.set()
    await wait_for(lambda: [e for e in player.feed.recent if e.kind == "stormed_off"])


@pytest.mark.asyncio
async def test_a_lean_in_that_fails_keeps_the_greeting_for_the_next():
    player = near_missing_player(RuntimeError("haiku down"), "Go on.", "Tell me more.")
    player.engine.rapport.sour("v1", PID, 0.5)
    for text in ("the wheel looks tired", "the wheel looks very tired", "the wheel looks worn out"):
        await player.whisper_to("v1", text)
        await wait_for(lambda: player._tasks == set())
    assert ["Greet the newcomer" in m for m in player.writer.moods] == [True, True, False]


@pytest.mark.asyncio
async def test_first_line_of_a_rumor_greets_by_rapport():
    player = near_missing_player("Go on.", "Tell me more.")
    player.engine.rapport.sour("v1", PID, 0.5)
    await player.whisper_to("v1", "the wheel looks tired")
    await wait_for(lambda: player._tasks == set())
    await player.whisper_to("v1", "the wheel looks very tired")
    await wait_for(lambda: player._tasks == set())
    first, second = player.writer.moods
    assert "curious" in first and "fond of the newcomer" in first and "Greet the newcomer" in first
    assert second == interest_words(interest(player)) and "newcomer" not in second


@pytest.mark.asyncio
async def test_a_retold_rumor_is_annoying_by_its_root_interest():
    player = await missed_once()
    engine = player.engine
    engine.rumors = Rumors(StubContex(), StubBaselines(0.5), Config(session="s1"))
    now = datetime.now()
    engine.rumors.open("w-1", "the tide tables", now, "v1", "Aldwin", None, player=PID)
    engine.rumors.open("w-2", "the tide tables again", now, "v1", "Aldwin", "w-1", player=PID)
    engine.rapport.move("v1", PID, "w-1", -0.5)
    engine.conversations[("v1", PID)] = {"id": "w-2", "answered": ["w-2"]}
    assert player.annoyed("v1")


def test_past_max_idle_runs_the_oldest_never_played_runs_go_whatever_their_age(clock):
    first = make("Ann")[0]
    first.config = replace(first.config, max_idle_runs=2)
    players = Players(None)
    runs = []
    for pid in ("p-1", "p-2", "p-played", "p-3"):
        clock[0] += 1
        runs.append(run_of(first, pid))
        players.add(runs[-1])
    runs[2].typing()
    assert players.prune() == [runs[0]]
    assert [p.id for p in players] == ["p-2", "p-played", "p-3"]


def durable(p):
    return (p.id, p.owner, p.place, p.state, p.ended_at, p.created_at, p.summary, p.goals, p.met, p.whispered_at,
            p._threads, p._held, p._hinted, p._said_at, p._typed_at, p.over())


def test_players_round_trip_as_json_with_no_sockets():
    player, town, _, engine, lineage = make("Bert")
    goal = Goal("gen-1", "Bert is a spy", "belief", evidence=("bert spies",), audience=("v1",), owner=PID)
    player.goals, player.met, player.place = [goal], {"gen-1"}, "tavern"
    player._threads["v1"] = {"id": "w-1", "need": "bread", "at": 5.0}
    player._held["v1"] = ("w-1", "w-1")
    player._hinted["v1"] = {1, 2}
    player._said_at["v1"], player._typed_at, player.whispered_at = 4.0, 6.0, 4.0
    player.connected()
    ended = Player(engine, town, lineage, Ids(), player.feed, player.config, player_id="p-2", owner="o-1")
    ended.end(Summary("lost", [("Bert is a spy", 40)], ["ran out"]))
    players = Players(player.feed)
    players.add(player)
    players.add(ended)
    trophy = Trophy("personal", ("Bert is a spy",), 2)
    players.award("o-1", trophy, "well done")
    players.cases[PID] = [Trophy("world", ("all hold it",), 3, long_shot=True)]
    assert players.any_playing()

    def wire(pid, owner):
        return Player(engine, town, lineage, Ids(), player.feed, player.config, player_id=pid, owner=owner)

    fresh = Players(player.feed)
    fresh.restore(json.loads(json.dumps(players.to_state())), wire)
    assert [durable(p) for p in fresh] == [durable(p) for p in players]
    assert fresh.get("p-2").finished.is_set() and fresh.get("p-2").summary.goals == [("Bert is a spy", 40)]
    assert fresh.live("o-1") is fresh.get("p-2") and fresh.live(PID) is fresh.get(PID)
    assert fresh.cases == players.cases and fresh.pending == {"o-1": [(trophy, "well done")]}
    assert fresh.get(PID).ever_active() and not fresh.get(PID).listening()
    assert not fresh.any_playing()
    fresh.get(PID).connected()
    assert fresh.any_playing()
