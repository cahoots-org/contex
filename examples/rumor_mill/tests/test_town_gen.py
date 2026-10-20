import json
import random

import pytest

from examples.rumor_mill.town_gen import (
    MAX_BATCH_ATTEMPTS, generate_newcomer, generate_villagers, load_or_generate,
    wire_relationships,
)
from examples.rumor_mill.models import Villager


def entry(i):
    return {
        "name": f"Name{i}", "occupation": "baker", "personality": "gruff",
        "grudge": "old feud",
        "needs": ["my oven keeps failing", "my daughter wants to leave", "flour prices"],
    }


class StubWriter:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def complete(self, system, user, max_tokens=400):
        self.calls += 1
        return self.text


def fenced(n):
    return "Here you go:\n```json\n" + json.dumps([entry(i) for i in range(n)]) + "\n```"


def ring(n):
    return [Villager(f"v{i:03d}", f"N{i}", "x", "y", "z", ["a", "b", "c"], {}) for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_generate_villagers_parses_fenced_json_and_assigns_ids():
    writer = StubWriter(fenced(10))

    villagers = await generate_villagers(writer, 12, random.Random(1))

    assert [v.id for v in villagers] == [f"v{i:03d}" for i in range(1, 13)]
    assert writer.calls == 2
    assert all(v.relationships == {} for v in villagers)


@pytest.mark.asyncio
async def test_every_villager_has_three_to_five_needs():
    villagers = await generate_villagers(StubWriter(fenced(5)), 5, random.Random(1))

    assert all(3 <= len(v.needs) <= 5 for v in villagers)


def test_wire_relationships_bounds_and_reciprocity():
    villagers = ring(30)
    wire_relationships(villagers, random.Random(7))
    by_id = {v.id: v for v in villagers}
    reverse = {"spouse": "spouse", "rival": "rival", "friend": "friend",
               "debtor": "creditor", "creditor": "debtor"}

    for v in villagers:
        assert 2 <= len(v.relationships) <= 4
        for other, kind in v.relationships.items():
            assert by_id[other].relationships[v.id] == reverse[kind]


@pytest.mark.asyncio
async def test_load_or_generate_reuses_seed_file(tmp_path):
    path = tmp_path / "seed.json"
    writer = StubWriter(fenced(10))

    first = await load_or_generate(str(path), writer, 10, random.Random(1))
    calls = writer.calls
    second = await load_or_generate(str(path), writer, 10, random.Random(2))

    assert writer.calls == calls
    assert second == first
    assert all(v.relationships for v in second)


@pytest.mark.asyncio
async def test_newcomer_relates_to_one_or_two_existing():
    existing = ring(10)
    writer = StubWriter(json.dumps(entry(99)))

    newcomer = await generate_newcomer(writer, existing, random.Random(3))

    assert newcomer.id == "v011"
    assert 1 <= len(newcomer.relationships) <= 2
    assert set(newcomer.relationships) <= {v.id for v in existing}
    assert all(v.relationships == {} for v in existing)


class SequenceWriter:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    async def complete(self, system, user, max_tokens=400):
        self.calls += 1
        return self.texts[min(self.calls, len(self.texts)) - 1]


@pytest.mark.asyncio
async def test_generate_villagers_gives_up_after_max_attempts():
    writer = SequenceWriter("[]")

    with pytest.raises(ValueError, match="0 of 5"):
        await generate_villagers(writer, 5, random.Random(1))

    assert writer.calls == MAX_BATCH_ATTEMPTS


@pytest.mark.asyncio
async def test_generate_villagers_retries_invalid_replies():
    few_needs = json.dumps([{**entry(0), "needs": ["only", "two"]}])
    writer = SequenceWriter(json.dumps(entry(0)), few_needs, fenced(3))

    villagers = await generate_villagers(writer, 3, random.Random(1))

    assert len(villagers) == 3
    assert writer.calls == 3


@pytest.mark.asyncio
async def test_newcomer_rejects_empty_list():
    with pytest.raises(ValueError):
        await generate_newcomer(StubWriter("[]"), ring(3), random.Random(1))


@pytest.mark.asyncio
async def test_wrapper_object_reply_yields_villagers():
    writer = StubWriter(json.dumps({"villagers": [entry(i) for i in range(3)]}))

    villagers = await generate_villagers(writer, 3, random.Random(1))

    assert [v.name for v in villagers] == ["Name0", "Name1", "Name2"]


@pytest.mark.asyncio
async def test_needs_string_is_split_into_needs():
    multi = "- my oven keeps failing\n- my daughter wants to leave\n2. flour prices"
    sentences = "My oven fails. My daughter wants to leave! Flour is dear."
    reply = json.dumps([{**entry(0), "needs": multi}, {**entry(1), "needs": sentences}])

    villagers = await generate_villagers(StubWriter(reply), 2, random.Random(1))

    assert villagers[0].needs == ["my oven keeps failing", "my daughter wants to leave", "flour prices"]
    assert villagers[1].needs == ["My oven fails.", "My daughter wants to leave!", "Flour is dear."]


@pytest.mark.asyncio
async def test_one_sentence_needs_string_is_rejected():
    writer = StubWriter(json.dumps([{**entry(0), "needs": "Just one worry."}]))

    with pytest.raises(ValueError, match="0 of 1"):
        await generate_villagers(writer, 1, random.Random(1))

    assert writer.calls == MAX_BATCH_ATTEMPTS


@pytest.mark.asyncio
async def test_newcomer_relates_only_to_living_and_keeps_ids_unique():
    living, gone = ring(3), [Villager(f"v{i:03d}", f"Gone{i}", "baker", "x", "y", [], {}) for i in range(10, 60)]

    for seed in range(20):
        newcomer = await generate_newcomer(
            StubWriter(json.dumps(entry(99))), living, random.Random(seed), departed=gone,
        )
        assert newcomer.id == "v060"
        assert set(newcomer.relationships) <= {v.id for v in living}
