import asyncio

import pytest

import time
from datetime import datetime

from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Refusal, echoes
from examples.rumor_mill.mind import mind_project
from examples.rumor_mill.tests.helpers import PID, StubBaselines, StubContex, wait_for
from examples.rumor_mill.tests.helpers import make_player as make
from examples.rumor_mill.town import NEWCOMER


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
    assert (utterance.id, utterance.speaker, utterance.speaker_name) == ("w-1", NEWCOMER, "The newcomer")
    assert (utterance.verb, utterance.place, utterance.parent) == ("whispered to you", "tavern", None)
    assert utterance.text == "the miller waters his flour"
    assert lineage.is_player_rooted("w-1")
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
    assert player._brush_offs == set()
    assert not [e for e in player.feed.recent if e.kind == "whisper_status"]


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
        self.gate = gate

    async def turn_away(self, villager):
        line = self.lines.pop(0)
        if isinstance(line, Exception):
            raise line
        return line

    async def go_on(self, villager, newcomer_line):
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
    player, _, _, engine, _ = make("Marta")
    suspect(player)
    assert await player.whisper("Marta", "hello") == Refusal("Marta turns away from you.")
    await asyncio.sleep(0.15)
    assert engine.delivered == []
    assert player._brush_offs == set()
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
    assert lines == [] and player._brush_offs == set()
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
    assert lines == [] and player._brush_offs == set()
    assert player.engine.suspicion.score("v1") == pytest.approx(player.config.suspicion_whisper_bump)


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
    assert lines == [] and player._brush_offs == set()


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
    engine.conversations["v1"] = {"id": "w-1", "answered": ["w-1"]}
    assert await player.whisper("Marta", "odd since the frost, too") is None
    first, reply = (u for u, _ in engine.delivered)
    assert first.conversation is None
    assert (reply.id, reply.parent, reply.depth, reply.conversation) == ("w-2", None, 0, "w-1")
    assert lineage.is_whisper("w-2") and engine.facts.get("w-2").conversation == "w-1"
    player.feed.on_delivery(reply, ["v1"])
    assert player.feed.recent[-1].data["conversation"] == "w-1"
    player.close()


@pytest.mark.asyncio
async def test_a_missed_reply_ends_the_conversation():
    player, _ = make_hinting(StubWriter("Wheel's groaning."))
    player.engine.conversations["v1"] = {"id": "w-0", "answered": ["w-0"]}
    await player.whisper_to("v1", "the tide tables were misprinted")
    assert "v1" not in player.engine.conversations
    player.close()


@pytest.mark.asyncio
async def test_a_refusal_ends_the_conversation():
    player, _, _, engine, _ = make("Marta")
    engine.conversations["v1"] = {"id": "w-0", "answered": ["w-0"]}
    suspect(player)
    assert isinstance(await player.whisper("Marta", "hello"), Refusal)
    assert "v1" not in engine.conversations


@pytest.mark.asyncio
async def test_a_missed_reply_leaves_a_newer_conversation_open():
    player, _ = make_hinting(StubWriter("Wheel's groaning."))
    conversations = player.engine.conversations
    conversations["v1"] = {"id": "w-0", "answered": ["w-0"]}

    async def miss_after_a_new_conversation_opens(target, utterance, since, hint_need=None):
        conversations["v1"] = {"id": "w-9", "answered": ["w-9"]}
        return []

    player.check = miss_after_a_new_conversation_opens
    await player.whisper_to("v1", "the tide tables were misprinted")
    assert player.engine.delivered[0][0].conversation == "w-0"
    assert conversations["v1"]["id"] == "w-9"
    player.close()


def hinted_player(*lines):
    """A player whose misses are brushed off with these lines."""
    player, _ = make_hinting(StubWriter(*lines))
    player.engine.baselines = StubBaselines(0.5)
    return player


async def miss(player):
    await player.whisper_to("v1", "the tide tables were misprinted")
    await wait_for(lambda: player._brush_offs == set())


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
async def test_a_thread_takes_two_whispers_then_starts_a_new_card():
    player = hinted_player("Wheel's groaning.", "Girl wants the city.", "Rats again.")
    for _ in range(3):
        await miss(player)
    assert conversations(player) == [None, "w-1", None]


@pytest.mark.asyncio
async def test_a_thread_closes_at_the_next_part_of_the_day():
    player = hinted_player("Wheel's groaning.", "Girl wants the city.")
    player.game = Game(player.feed)
    await miss(player)
    player.game.tick()
    await miss(player)
    assert conversations(player) == [None, None]


@pytest.mark.asyncio
async def test_a_reply_raises_suspicion_like_any_whisper():
    player = hinted_player("Wheel's groaning.", "Girl wants the city.")
    await miss(player)
    await miss(player)
    assert player.engine.suspicion.score("v1") == pytest.approx(2 * player.config.suspicion_whisper_bump)


@pytest.mark.asyncio
async def test_an_open_conversation_takes_precedence_over_a_thread():
    player = hinted_player("Wheel's groaning.")
    await miss(player)
    player.engine.conversations["v1"] = {"id": "w-9", "answered": False}
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
async def test_a_near_miss_on_the_last_try_is_a_brush_off():
    player = near_missing_player("Wheel's groaning.", "Girl wants the city.")
    await miss(player)
    player.engine.contex = StubContex({NEEDS[1]: landing(0.495, "utt:w-2")})
    await miss(player)
    assert [s["state"] for s in statuses(player)] == ["checking", "near_miss", "near_miss", "checking", "brushed_off", "brushed_off"]
    assert "v1" not in player._threads


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
    assert player._brush_offs == set() and "v1" not in player._threads


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
