from types import SimpleNamespace

import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.usage import CountingJev, Usage


def test_cost_uses_configured_prices():
    usage = Usage(Config())
    usage.record("haiku", 1_000_000, 200_000)
    usage.record("jev", 2_000_000, 500)
    assert usage.cost() == pytest.approx(1.0 + 1.0 + 0.08)
    custom = Usage(Config(haiku_input_per_mtok=2.0, haiku_output_per_mtok=0.0))
    custom.record("haiku", 500_000, 100_000)
    assert custom.cost() == pytest.approx(1.0)


def test_summary_format_and_zero():
    usage = Usage(Config())
    assert usage.summary() == "Estimated cost so far: $0.00"
    usage.record("jev", 1000, 0)
    assert usage.summary() == "Estimated cost so far: less than $0.01 (Jev 1k in)"
    usage = Usage(Config())
    usage.record("haiku", 41_200, 4_800)
    usage.record("jev", 220_400, None)
    assert usage.summary() == "Estimated cost so far: $0.07 (Haiku 41k in / 5k out, Jev 220k in)"
    usage.record("jev", 1_500_000, 0)
    assert "Jev 1.7M in" in usage.summary()
    assert usage.to_json()["summary"] == usage.summary()


class StubJev:
    def __init__(self, fail=False):
        self.fail = fail

    async def system_one(self, state, questions):
        if self.fail:
            raise RuntimeError("down")
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=900, output_tokens=None), nouls={})


@pytest.mark.asyncio
async def test_counting_jev_records_answers_and_ignores_failures():
    usage = Usage(Config())
    response = await CountingJev(StubJev(), usage).system_one(state={}, questions={})
    assert response.nouls == {}
    with pytest.raises(RuntimeError):
        await CountingJev(StubJev(fail=True), usage).system_one(state={}, questions={})
    assert usage.tokens["jev"] == [900, 0]


def test_record_tallies_per_player_and_in_total():
    usage = Usage(Config())
    usage.record("haiku", 1_000_000, 0, player="p-a")
    usage.record("jev", 2_000_000, 0, player="p-b")
    usage.record("haiku", 1_000_000, 0)
    assert usage.cost("p-a") == pytest.approx(1.0)
    assert usage.cost("p-b") == pytest.approx(0.08)
    assert usage.cost("p-c") == 0.0
    assert usage.cost() == pytest.approx(2.08)
    assert usage.tokens["haiku"] == [2_000_000, 0]


def test_summary_for_a_player_shows_only_their_share():
    usage = Usage(Config())
    usage.record("haiku", 1_000_000, 200_000, player="p-a")
    usage.record("haiku", 3_000_000, 0, player="p-b")
    assert usage.summary("p-a") == "Your rumors have cost about $2.00 so far"
    assert usage.summary("p-c") == "Your rumors have cost about $0.00 so far"
    assert usage.summary() == "Estimated cost so far: $5.00 (Haiku 4.0M in / 200k out)"
    assert usage.to_json("p-a") == {"summary": usage.summary("p-a"), "cost": 2.0}
