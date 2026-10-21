import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from examples.rumor_mill import rumors as rumors_mod
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.tests.helpers import StubBaselines, StubContex

URL = os.environ.get("RUMOR_MILL_CONTEX_URL")
AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def hop(uid, speaker="Bert", text="the miller waters his flour"):
    return Utterance(uid, "v1", speaker, "tavern", text, "w-1")


async def make(results=None, error=None, day=100.0):
    contex = StubContex(results, error)
    rumors = Rumors(contex, StubBaselines(0.5), Config(session="s1", day_seconds=day))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta")
    return rumors, contex


@pytest.mark.asyncio
async def test_add_publishes_the_words_only():
    _, contex = await make()
    assert contex.published == [
        ("rumor-mill:s1:rumors", "rumor:w-1", {"id": "w-1", "text": "the miller waters his flour"}),
    ]


@pytest.mark.asyncio
async def test_on_topic_clears_the_whisper_baseline_plus_margin():
    rumors, contex = await make({
        "close": [Match("rumor:w-2", {}, 0.9), Match("rumor:w-1", {}, 0.53)],
        "near": [Match("rumor:w-1", {}, 0.51)],
        "other": [Match("rumor:w-2", {}, 0.9)],
    })
    assert await rumors.on_topic("w-1", "close")
    assert not await rumors.on_topic("w-1", "near")
    assert not await rumors.on_topic("w-1", "other")
    project, _, kwargs = contex.queries[0]
    assert project == "rumor-mill:s1:rumors" and kwargs["threshold"] == 0
    assert kwargs["since"] == (AT - timedelta(seconds=5)).isoformat()


@pytest.mark.asyncio
async def test_a_failed_check_is_off_topic_and_logs_ids_only(caplog):
    rumors, _ = await make(error=RuntimeError("down"))
    with caplog.at_level(logging.WARNING):
        assert not await rumors.on_topic("w-1", "close")
    assert "w-1" in caplog.text and "0." not in caplog.text


class SlowBaselines:
    """Measures 0.5, but only once released; fails the first measure when told to."""

    def __init__(self, fail_first=False):
        self.release = asyncio.Event()
        self.fail_first = fail_first
        self.calls = 0

    async def measure(self, text):
        self.calls += 1
        await self.release.wait()
        if self.fail_first and self.calls == 1:
            raise RuntimeError("down")
        return 0.5


@pytest.mark.asyncio
async def test_a_hop_checked_before_the_baseline_waits_for_it():
    contex = StubContex({"close": [Match("rumor:w-1", {}, 0.53)]})
    baselines = SlowBaselines()
    rumors = Rumors(contex, baselines, Config(session="s1"))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta")
    check = asyncio.create_task(rumors.on_topic("w-1", "close"))
    await asyncio.sleep(0.01)
    assert not check.done()
    baselines.release.set()
    assert await check
    assert not rumors.record("w-1", hop("u-1"), {"v2": "Ann"}, True)
    assert rumors.cards()[0]["drifted"] is False


@pytest.mark.asyncio
async def test_a_cancelled_check_leaves_the_baseline_for_the_next():
    contex = StubContex({"close": [Match("rumor:w-1", {}, 0.53)]})
    baselines = SlowBaselines()
    rumors = Rumors(contex, baselines, Config(session="s1"))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta")
    first = asyncio.create_task(rumors.on_topic("w-1", "close"))
    await asyncio.sleep(0.01)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    baselines.release.set()
    assert await rumors.on_topic("w-1", "close")
    assert baselines.calls == 1


@pytest.mark.asyncio
async def test_close_cancels_a_pending_measure():
    rumors = Rumors(StubContex(), SlowBaselines(), Config(session="s1"))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta")
    measuring = rumors._cards["w-1"].measuring
    rumors.close()
    await asyncio.gather(measuring, return_exceptions=True)
    assert measuring.cancelled()


@pytest.mark.asyncio
async def test_a_failed_baseline_is_measured_again_by_the_next_check():
    contex = StubContex({"close": [Match("rumor:w-1", {}, 0.53)]})
    baselines = SlowBaselines(fail_first=True)
    baselines.release.set()
    rumors = Rumors(contex, baselines, Config(session="s1"))
    await rumors.add("w-1", "the miller waters his flour", AT, "v1", "Marta")
    assert await rumors.on_topic("w-1", "close")
    assert baselines.calls == 2


@pytest.mark.asyncio
async def test_tallies(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rumors_mod.time, "time", lambda: now[0])
    rumors, _ = await make(day=100.0)
    assert not rumors.record("w-1", hop("u-1"), {"v2": "Ann", "v3": "Cal"}, True)
    now[0] = 1150.0
    assert not rumors.record("w-1", hop("u-2", "Ann"), {"v3": "Cal", "v4": "Dee"}, True)
    assert rumors.record("w-1", hop("u-3", "Cal", "the weather"), {"v5": "Eve"}, False)
    assert not rumors.record("w-1", hop("u-4", "Eve", "the weather again"), {"v6": "Fay"}, False)
    card, = rumors.cards()
    assert (card["uid"], card["text"], card["target_id"], card["target_name"]) == (
        "w-1", "the miller waters his flour", "v1", "Marta",
    )
    assert [h["uid"] for h in card["hops"]] == ["u-1", "u-2"]
    assert card["hops"][1] == {
        "uid": "u-2", "speaker": "Ann", "listeners": ["Cal", "Dee"], "text": "the miller waters his flour",
    }
    assert (card["heard"], card["still"], card["drifted"]) == (4, 2, True)


@pytest.mark.skipif(not URL, reason="RUMOR_MILL_CONTEX_URL not set")
@pytest.mark.asyncio
async def test_live_paraphrase_stays_on_topic_and_a_reply_does_not(tmp_path, caplog):
    cfg = Config(contex_url=URL, session=f"rumor-{uuid.uuid4().hex[:8]}", seed_path=str(tmp_path / "seed.json"))
    whisper = "The miller waters down his flour and sells it short to the whole village."
    paraphrase = "They say the miller has been thinning his flour with water and cheating everyone."
    reply = "I don't know about that, but my back has been aching since the frost."
    async with ContexClient(URL) as client:
        rumors = Rumors(client, Baselines(client, cfg), cfg)
        await rumors.add("w-1", whisper, datetime.now(UTC), "v1", "Marta")
        with caplog.at_level(logging.DEBUG, logger=rumors_mod.__name__):
            assert await rumors.on_topic("w-1", paraphrase)
            assert not await rumors.on_topic("w-1", reply)
    print("\n".join(r.getMessage() for r in caplog.records))
