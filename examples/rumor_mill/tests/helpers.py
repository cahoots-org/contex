"""Test doubles and helpers shared by the Rumor Mill tests."""

import asyncio
from types import SimpleNamespace

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import Player
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import Town

PID = "p-1"
TIMEOUT = 2.0


class Always:
    """A stand-in for random.Random whose random() is always the same value."""

    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


class FakeMind:
    def __init__(self, villager):
        self.villager = villager
        self.queue: asyncio.Queue = asyncio.Queue()
        self.results: list[list[Match]] = []
        self.matches: list[Match] = []
        self.heard = []
        self.seen = set()
        self.polls = 0

    async def open(self):
        pass

    async def hear(self, key, data):
        self.heard.append((key, data))

    async def poll(self):
        self.polls += 1
        fresh = self.results.pop(0) if self.results else [Match(f"k{self.polls}", {"id": "u-0", "text": "news"})]
        self.matches = fresh
        self.seen = {m.data_key for m in fresh}
        return fresh

    async def renew(self):
        pass

    async def pushes(self):
        while True:
            yield await self.queue.get()


def make(vid):
    return Villager(vid, vid.upper(), "baker", "calm", "none", ["bread"], {})


async def wait_for(predicate, timeout=TIMEOUT):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class StubJev:
    def __init__(self, answers, scores=None):
        self.answers = answers
        self.scores = scores
        self.questions = {}
        self.state = None

    async def system_one(self, state, questions):
        self.state = state
        self.questions = questions
        choices = {
            k: SimpleNamespace(choice=v, probabilities={v: 1.0})
            for k, v in self.answers.items() if k in questions
        }
        if self.scores is None:
            return SimpleNamespace(choices=choices)
        scores = {k: SimpleNamespace(score=v, confidence=0.9) for k, v in self.scores.items() if k in questions}
        return SimpleNamespace(choices=choices, scores=scores)


class StubBaselines:
    """A fixed threshold per need, with a default for the rest."""

    def __init__(self, default, **by_need):
        self.default = default
        self.by_need = by_need

    def threshold(self, need):
        return self.by_need.get(need, self.default)

    def matches(self, need, similarity):
        return similarity >= self.threshold(need)

    async def load(self, texts):
        pass

    async def measure(self, text):
        return self.default


class StubContex:
    """Answers each query from results keyed by query text; records every call."""

    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.default = []
        self.queries = []
        self.published = []

    async def publish(self, project_id, data_key, data):
        self.published.append((project_id, data_key, data))

    async def query(self, project_id, query, **kwargs):
        self.queries.append((project_id, query, kwargs))
        if self.error:
            raise self.error
        return self.results.get(query, self.default)


class FakeEngine:
    def __init__(self, suspicion=None, contex=None, baselines=None):
        self.delivered = []
        self.suspicion = suspicion
        self.contex = contex or StubContex()
        self.baselines = baselines or StubBaselines(0.5)
        self.direct = []
        self.rumors = None
        self.facts = Facts()
        self.free_at = 0.0
        self.conversations = {}
        self.heard = []
        self.recalls = []

    async def recall_heard(self, vid, text, exclude):
        self.recalls.append((vid, text, exclude))
        return self.heard

    def end_conversation(self, vid, player=None):
        for key in [k for k in self.conversations if k[0] == vid and player in (None, k[1])]:
            del self.conversations[key]

    async def deliver(self, utterance, listener_ids):
        self.delivered.append((utterance, listener_ids))

    def hear_directly(self, vid, match):
        self.direct.append((vid, match))
        return True

    def cooldown_ends(self, vid):
        return self.free_at


def stub_player(pid=PID, place="square"):
    """A player standing at place whose game is not over."""
    return SimpleNamespace(id=pid, place=place, over=lambda: False)


def make_player(*names, cooldown=0.0):
    town, lines = Town(), []
    config = Config(cooldown_seconds=cooldown)
    engine = FakeEngine(Suspicion(config, town))
    lineage = Lineage()
    for i, name in enumerate(names, 1):
        town.add(Villager(f"v{i}", name, "baker", "calm", "none", [], {}), "tavern")
    feed = Feed(lineage, town, out=lines.append)
    player = Player(engine, town, lineage, Ids(), feed, config, player_id=PID)
    return player, town, lines, engine, lineage


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
