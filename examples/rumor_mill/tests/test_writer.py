import pytest

from examples.rumor_mill.decide import Decision
from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Villager
from examples.rumor_mill.usage import Usage
from examples.rumor_mill.writer import Writer


class StubMessages:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("Block", (), {"text": self.texts.pop(0)})()
        usage = type("Usage", (), {"input_tokens": 120, "output_tokens": 30})()
        return type("Response", (), {"content": [block], "usage": usage})()


class StubClient:
    def __init__(self, *texts):
        self.messages = StubMessages(texts)


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
