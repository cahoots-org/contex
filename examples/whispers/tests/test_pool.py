import asyncio
import json

import pytest

from examples.whispers.models import Villager
from examples.whispers.pool import DEFAULT_ACCUSATIONS, DEFAULT_CLAIMS, Entry, clean, load_pool


class CountingBelief:
    def __init__(self, band=1):
        self.asked = []
        self.value = band

    async def band(self, text):
        self.asked.append(text)
        return self.value


class FakeWriter:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def goal_pool(self, villagers):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def villagers():
    return [Villager("v001", "Ada Baker", "baker", "sharp", "", [], {})]


def test_clean_drops_long_second_person_named_and_duplicate():
    texts = [
        "is stealing from the tithe box",
        "x" * 81,
        "owes you money",
        "Ada is a thief",
        "is stealing from the tithe box",
        "keeps yourself to himself",
        "hides ada's flour",
    ]
    assert clean(texts, ["Ada"]) == ["is stealing from the tithe box"]
    assert clean(["fed the adage"], ["Ada"]) == ["fed the adage"]


@pytest.mark.asyncio
async def test_load_pool_writes_and_reuses_file(tmp_path):
    path = str(tmp_path / "goals.json")
    writer = FakeWriter({"accusations": ["waters down the ale"], "claims": ["the well is cursed"]})
    belief = CountingBelief(2)

    pool = await load_pool(path, writer, belief, villagers())

    assert pool.accusations == (Entry("waters down the ale", 2),)
    assert pool.claims == (Entry("the well is cursed", 2),)
    assert belief.asked == ["someone waters down the ale", "the well is cursed"]
    assert json.load(open(path)) == {
        "accusations": [{"text": "waters down the ale", "band": 2}],
        "claims": [{"text": "the well is cursed", "band": 2}],
    }

    again = await load_pool(path, FakeWriter(RuntimeError("no")), CountingBelief(), villagers())

    assert again == pool
    assert writer.calls == 1


@pytest.mark.asyncio
async def test_pool_cleans_and_rescores_a_bad_file(tmp_path):
    path = tmp_path / "goals.json"
    path.write_text(json.dumps({
        "accusations": [{"text": "keeps a ledger", "band": 3}, {"text": "cheats you", "band": 1}, {"text": "hides coin"}],
        "claims": [{"text": "Ada saw a ghost", "band": 0}, "the moon is hollow"],
    }))
    belief = CountingBelief(1)

    pool = await load_pool(str(path), FakeWriter(RuntimeError("unused")), belief, villagers())

    assert pool.accusations == (Entry("keeps a ledger", 3), Entry("hides coin", 1))
    assert pool.claims == (Entry("the moon is hollow", 1),)
    assert belief.asked == ["someone hides coin", "the moon is hollow"]


@pytest.mark.asyncio
async def test_load_pool_falls_back_to_defaults_when_writer_fails(tmp_path):
    pool = await load_pool(str(tmp_path / "goals.json"), FakeWriter(ValueError("bad")), CountingBelief(), villagers())

    assert {e.text for e in pool.accusations} == set(DEFAULT_ACCUSATIONS)
    assert {e.text for e in pool.claims} == set(DEFAULT_CLAIMS)
    assert not (tmp_path / "goals.json").exists()


@pytest.mark.asyncio
async def test_load_pool_strips_padded_entries(tmp_path):
    reply = {"accusations": ["  is late \n"], "claims": [" the well is cursed "]}
    pool = await load_pool(str(tmp_path / "goals.json"), FakeWriter(reply), CountingBelief(), villagers())
    assert pool.accusations == (Entry("is late", 1),)
    assert pool.claims == (Entry("the well is cursed", 1),)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{"accusations": ["is late"], "claims": []}, {"accusations": ["owes you"], "claims": ["Ada did it"]}])
async def test_load_pool_defaults_a_list_with_nothing_usable_and_saves_nothing(tmp_path, reply):
    pool = await load_pool(str(tmp_path / "goals.json"), FakeWriter(reply), CountingBelief(), villagers())
    assert {e.text for e in pool.claims} == set(DEFAULT_CLAIMS)
    assert pool.accusations
    assert not (tmp_path / "goals.json").exists()


@pytest.mark.asyncio
async def test_load_pool_survives_an_unwritable_path(tmp_path):
    path = str(tmp_path / "missing" / "goals.json")
    pool = await load_pool(path, FakeWriter({"accusations": ["is late"], "claims": ["the well is cursed"]}), CountingBelief(), villagers())
    assert pool.accusations == (Entry("is late", 1),)
    assert pool.claims == (Entry("the well is cursed", 1),)


class DownBelief:
    async def band(self, text):
        return None


@pytest.mark.asyncio
async def test_load_pool_saves_nothing_when_a_band_could_not_be_scored(tmp_path):
    path = tmp_path / "goals.json"
    writer = FakeWriter({"accusations": ["waters down the ale"], "claims": ["the well is cursed"]})

    pool = await load_pool(str(path), writer, DownBelief(), villagers())

    assert pool.accusations == (Entry("waters down the ale", 2),)
    assert not path.exists()


@pytest.mark.asyncio
async def test_load_pool_scores_missing_bands_concurrently(tmp_path):
    started = 0
    both = asyncio.Event()

    class Gated:
        async def band(self, text):
            nonlocal started
            started += 1
            if started == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return 1

    writer = FakeWriter({"accusations": ["waters down the ale", "cheats at dice"], "claims": ["the well is cursed"]})
    pool = await load_pool(str(tmp_path / "goals.json"), writer, Gated(), villagers())
    assert [e.band for e in pool.accusations] == [1, 1]


def test_entries_are_tidied_to_read_mid_sentence():
    assert clean(["The well water has turned bitter.", "  waters the ale! ", "UFOs land at the mill."], []) == [
        "the well water has turned bitter", "waters the ale", "UFOs land at the mill",
    ]
