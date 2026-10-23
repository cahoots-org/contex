import re

import pytest

from examples.rumor_mill.decide import Decision
from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Villager
from examples.rumor_mill.usage import Usage
from examples.rumor_mill.tests.helpers import StubClient
from examples.rumor_mill.writer import SAY_MAX_TOKENS, WHISPER_FRAMING, Writer, first_line


def villager():
    return Villager("v001", "Ada", "baker", "sharp-tongued", "owes me flour", ["bread"], {})


@pytest.mark.asyncio
async def test_say_includes_style_and_audience_in_prompt():
    client = StubClient("He did it, I tell you.")
    writer = Writer(client, "haiku-test")
    decision = Decision("confront", None, "v002", "exaggerated")

    text = await writer.say(villager(), decision, "Bob stole a loaf", "Bob, to his face")

    call = client.messages.calls[0]
    prompt = call["system"] + call["messages"][0]["content"]
    assert text == "He did it, I tell you."
    assert call["model"] == "haiku-test"
    assert "bigger and more dramatic" in prompt
    assert "Bob, to his face" in prompt
    assert "Bob stole a loaf" in prompt
    assert "sharp-tongued" in prompt


@pytest.mark.asyncio
async def test_describe_group_parses_name_and_creed():
    client = StubClient('Sure:\n```json\n{"name": "The Quiet Hand", "creed": "Say less."}\n```')
    writer = Writer(client, "haiku-test")

    name, creed = await writer.describe_group(villager(), ["bread is life"])

    assert (name, creed) == ("The Quiet Hand", "Say less.")


@pytest.mark.asyncio
async def test_say_caps_tokens():
    client = StubClient("Hm.")
    await Writer(client, "haiku-test").say(villager(), Decision("speak", None, None, None), None, "all")
    assert client.messages.calls[0]["max_tokens"] == 80


@pytest.mark.asyncio
async def test_complete_records_usage():
    usage = Usage(Config())
    await Writer(StubClient("Hm.", "Ha."), "haiku-test", usage).complete("s", "u")
    assert usage.tokens["haiku"] == [120, 30]


@pytest.mark.asyncio
async def test_turn_away_prompts_with_profile_and_returns_one_line():
    client = StubClient('"I will not hear you out."\nSecond line.')
    line = await Writer(client, "haiku-test").turn_away(villager())
    assert line == "I will not hear you out."
    call = client.messages.calls[0]
    assert all(s in call["system"] for s in ("Ada", "baker", "sharp-tongued", "Refuse"))
    assert "owes me flour" not in call["system"]
    assert call["max_tokens"] == 60


@pytest.mark.asyncio
async def test_storm_off_prompts_with_profile_and_returns_one_line():
    client = StubClient('"I have heard quite enough."\nSecond line.')
    line = await Writer(client, "haiku-test").storm_off(villager())
    assert line == "I have heard quite enough."
    call = client.messages.calls[0]
    assert all(s in call["system"] for s in ("Ada", "baker", "sharp-tongued", "storm off"))
    assert call["max_tokens"] == 60


@pytest.mark.asyncio
async def test_brush_off_prompts_with_profile_and_concern_and_returns_one_line():
    client = StubClient('"Not now, my oven is cracked."\nAnd another thing.')
    line = await Writer(client, "haiku-test").brush_off(villager(), "my oven needs a new hearthstone")
    assert line == "Not now, my oven is cracked."
    call = client.messages.calls[0]
    assert all(s in call["system"] for s in ("Ada", "baker", "sharp-tongued", "Never repeat its wording"))
    assert "in keeping with your temperament: a kindly villager is busy and apologetic, a sour one curt" in call["system"]
    assert "grumbl" not in call["system"]
    assert "owes me flour" not in call["system"]
    assert "my oven needs a new hearthstone" in call["messages"][0]["content"]
    assert call["max_tokens"] == 80


@pytest.mark.asyncio
async def test_brush_off_may_name_the_subject_but_no_particulars():
    client = StubClient("Not now.")
    await Writer(client, "haiku-test").brush_off(villager(), "my oven needs a new hearthstone")
    system = client.messages.calls[0]["system"]
    assert (
        "You may name the subject itself, such as strange lights or the vines, "
        "but never a name, a number, or any particular. Never repeat its wording."
    ) in system
    assert "don't name the specific thing" not in system


@pytest.mark.asyncio
async def test_warn_asks_for_one_line_and_never_says_how_suspicious_anyone_is():
    client = StubClient('"Watch that newcomer, Bo."\nMore.')
    target = Villager("v002", "Bo", "miller", "dour", "", [], {})
    line = await Writer(client, "haiku-test").warn(villager(), target)
    assert line == "Watch that newcomer, Bo."
    call = client.messages.calls[0]
    prompt = call["system"] + call["messages"][0]["content"]
    assert all(s in prompt for s in ("Ada", "baker", "sharp-tongued", "Bo", "newcomer", "one short sentence"))
    assert "owes me flour" not in prompt
    assert "Never say how sure" in prompt
    assert re.search(r"\d\.\d|suspicion", prompt) is None
    assert call["max_tokens"] == 60


@pytest.mark.asyncio
async def test_reply_answers_the_newcomer_in_one_line():
    client = StubClient('"Aye, the well has been off since spring."\nShe wipes her hands.', "Hm.")
    writer = Writer(client, "haiku-test")
    line = "A newcomer whispered to you: the well water tastes odd"
    assert await writer.reply(villager(), line, []) == "Aye, the well has been off since spring."
    await writer.reply(villager(), line, ["Ada said: the well is fine"])
    plain, with_said = (c["system"] + c["messages"][0]["content"] for c in client.messages.calls)
    assert "the well water tastes odd" in plain and "whispered to you" not in plain
    assert "the well is fine" not in plain and "the well is fine" in with_said
    assert "suspic" in plain.lower()


@pytest.mark.asyncio
async def test_reply_mentions_what_they_heard_only_when_there_is_something():
    client = StubClient("Hm.", "Hm.")
    writer = Writer(client, "haiku-test")
    line = "A newcomer whispered to you: the miller waters his flour"
    await writer.reply(villager(), line, [])
    await writer.reply(villager(), line, [], ["Bo said: the miller waters his flour", "Cy said: flour is thin"])
    plain, with_heard = (c["system"] + c["messages"][0]["content"] for c in client.messages.calls)
    assert "What you heard earlier" not in plain and "If you already heard" not in plain
    assert "What you heard earlier: Bo said: the miller waters his flour / Cy said: flour is thin" in with_heard
    assert "If you already heard about this, say so and what you heard, in your own words." in with_heard


@pytest.mark.asyncio
async def test_say_and_reply_mention_a_grudge_only_when_there_is_one():
    client = StubClient("Hm.", "Hm.", "Hm.", "Hm.")
    writer = Writer(client, "haiku-test")
    kindly = Villager("v002", "Bea", "weaver", "warm", "", ["cloth"], {})
    decision = Decision("speak", None, None, None)

    await writer.say(kindly, decision, "Bob stole a loaf", "all")
    await writer.reply(kindly, "the well water tastes odd", [])
    await writer.say(villager(), decision, "Bob stole a loaf", "all")
    await writer.reply(villager(), "the well water tastes odd", [])

    kind_say, kind_reply, sour_say, sour_reply = (c["system"] for c in client.messages.calls)
    assert "grudge" not in (kind_say + kind_reply).lower()
    assert "Your grudge: owes me flour." in sour_say and "Your grudge: owes me flour." in sour_reply
    assert all("relish and curiosity, not malice" in s for s in (kind_say, sour_say))


@pytest.mark.asyncio
async def test_say_tells_it_as_a_juicy_secret_in_the_conspiratorial_manner():
    client = StubClient("Between us, he did it.")
    await Writer(client, "haiku-test").say(villager(), Decision("speak", None, None, "conspiratorial"), "Bob stole a loaf", "all")
    assert "Manner: Share it as a juicy secret, in a hushed, confiding way, using words only." in client.messages.calls[0]["messages"][0]["content"]


def test_first_line_drops_stage_directions():
    raw = "*lowers voice conspiratorially* Mark my words, the taxes are *really* climbing."
    assert first_line(raw) == "Mark my words, the taxes are climbing."


@pytest.mark.asyncio
async def test_go_on_urges_the_newcomer_on_in_one_line():
    client = StubClient('"Go on, I am listening."\nMore.')
    line = await Writer(client, "haiku-test").go_on(villager(), "the oven looks tired")
    assert line == "Go on, I am listening."
    call = client.messages.calls[0]
    assert all(s in call["system"] for s in ("Ada", "baker", "sharp-tongued", "under 15 words"))
    assert "owes me flour" not in call["system"]
    assert "the oven looks tired" in call["messages"][0]["content"]
    assert call["max_tokens"] == 40


def test_whisper_framing_is_a_newcomer():
    assert WHISPER_FRAMING.sub("", "A newcomer whispered to you: hi") == "hi"


@pytest.mark.asyncio
async def test_answer_names_the_topic_and_what_was_heard_in_one_line():
    client = StubClient("Tom told me at the mill, aye.", "Not a word.")
    writer = Writer(client, "haiku-test")
    line = await writer.answer(villager(), "the mill", ["Tom at the mill: the mill is haunted"])
    assert line == "Tom told me at the mill, aye."
    call = client.messages.calls[0]
    user = call["messages"][0]["content"]
    assert call["max_tokens"] == SAY_MAX_TOKENS
    assert "the mill" in user and "Tom at the mill: the mill is haunted" in user
    assert "Ada" in call["system"] and "Never mention suspicion" in call["system"]
    await writer.answer(villager(), "the well", [])
    assert "nothing about it" in client.messages.calls[1]["messages"][0]["content"]
    assert "You know everyone in the village" in call["system"]


@pytest.mark.asyncio
async def test_answer_is_told_who_the_neighbours_in_the_question_are():
    client = StubClient("The tanner? Keeps to himself.")
    writer = Writer(client, "haiku-test")
    await writer.answer(villager(), "Everard Coke", [], ["Everard Coke, the tanner"])
    assert "Neighbours you know: Everard Coke, the tanner" in client.messages.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_deny_is_one_flat_line_naming_no_newcomer():
    client = StubClient('"That is a lie, plain and simple."\nMore.')
    line = await Writer(client, "haiku-test").deny(villager(), "Ada steals geese")
    assert line == "That is a lie, plain and simple."
    call = client.messages.calls[0]
    prompt = call["system"] + call["messages"][0]["content"]
    assert all(s in prompt for s in ("Ada", "baker", "sharp-tongued", "Ada steals geese", "deny"))
    assert "newcomer" in call["system"] and "Never name" in call["system"]
    assert call["max_tokens"] == 80


@pytest.mark.asyncio
async def test_writer_goal_pool_parses_and_tolerates_junk():
    client = StubClient('```json\n{"accusations": ["waters the ale"], "claims": ["the well is cursed"]}\n```', "Sorry, no.")
    writer = Writer(client, "haiku-test")

    assert await writer.goal_pool([villager()]) == {"accusations": ["waters the ale"], "claims": ["the well is cursed"]}
    assert await writer.goal_pool([villager()]) == {}
    call = client.messages.calls[0]
    prompt = call["system"] + call["messages"][0]["content"]
    assert "baker" in prompt and "sharp-tongued" in prompt and "owes me flour" in prompt
