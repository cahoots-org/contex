import asyncio
import json
import os

import pytest

from examples.rumor_mill.calibration import CALIBRATION, FALLBACK_BASELINE, Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match

URL = os.environ.get("RUMOR_MILL_CONTEX_URL")


class FakeContex:
    def __init__(self, scores=None):
        self.published: list[tuple[str, str, dict]] = []
        self.queries: list[tuple] = []
        self.scores = scores if scores is not None else [0.7, 0.8]

    async def publish(self, project_id, data_key, data):
        self.published.append((project_id, data_key, data))

    async def query(self, project_id, query, top_k=3, threshold=None, **_):
        self.queries.append((project_id, query, top_k, threshold))
        return [Match(f"cal:{i}", {}, s) for i, s in enumerate(self.scores)]


def config(tmp_path, **kw):
    return Config(session="s1", seed_path=str(tmp_path / "seed.json"), **kw)


def cached(path):
    return json.loads(path.read_text())["baselines"]


@pytest.mark.asyncio
async def test_baseline_is_mean_of_calibration_scores(tmp_path):
    contex = FakeContex([0.7, 0.8, None])
    baselines = Baselines(contex, config(tmp_path))
    await baselines.load(["my bread is stale"])
    assert baselines.get("my bread is stale") == pytest.approx(0.75)
    assert contex.queries == [("rumor-mill:s1:calibration", "my bread is stale", len(CALIBRATION), 0)]
    assert len(contex.published) == len(CALIBRATION)
    project, key, data = contex.published[0]
    assert (project, key) == ("rumor-mill:s1:calibration", "cal:0")
    assert data["text"] == f"The newcomer whispered to you at the square: {CALIBRATION[0]}"
    assert data["id"] == "cal-0"


@pytest.mark.asyncio
async def test_cache_hit_skips_queries(tmp_path):
    cfg = config(tmp_path)
    await Baselines(FakeContex(), cfg).load(["a"])
    contex = FakeContex()
    again = Baselines(contex, cfg)
    await again.load(["a"])
    assert contex.queries == [] and contex.published == []
    assert again.get("a") == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_new_text_is_computed_even_with_a_cache(tmp_path):
    cfg = config(tmp_path)
    await Baselines(FakeContex(), cfg).load(["a"])
    contex = FakeContex([0.5])
    baselines = Baselines(contex, cfg)
    await baselines.load(["a", "b"])
    assert [q[1] for q in contex.queries] == ["b"]
    assert baselines.get("b") == 0.5 and baselines.get("a") == pytest.approx(0.75)
    assert cached(tmp_path / "seed.json.baselines.json") == {"a": pytest.approx(0.75), "b": 0.5}


@pytest.mark.asyncio
async def test_corrupt_cache_is_ignored_and_rewritten(tmp_path):
    cfg = config(tmp_path)
    path = tmp_path / "seed.json.baselines.json"
    path.write_text("{not json")
    baselines = Baselines(FakeContex(), cfg)
    await baselines.load(["a"])
    assert baselines.get("a") == pytest.approx(0.75)
    assert cached(path) == {"a": pytest.approx(0.75)}


@pytest.mark.asyncio
async def test_cache_from_another_calibration_is_recomputed(tmp_path):
    path = tmp_path / "seed.json.baselines.json"
    path.write_text(json.dumps({"calibration": "other", "baselines": {"a": 0.9}}))
    contex = FakeContex()
    baselines = Baselines(contex, config(tmp_path))
    await baselines.load(["a"])
    assert [q[1] for q in contex.queries] == ["a"]
    assert cached(path) == {"a": pytest.approx(0.75)}


@pytest.mark.asyncio
async def test_no_scores_falls_back_without_failing(tmp_path):
    baselines = Baselines(FakeContex([]), config(tmp_path))
    await baselines.load(["a"])
    assert baselines.get("a") == FALLBACK_BASELINE
    assert not (tmp_path / "seed.json.baselines.json").exists() or "a" not in cached(
        tmp_path / "seed.json.baselines.json")


@pytest.mark.asyncio
async def test_fallback_is_never_cached_and_is_recomputed_later(tmp_path):
    contex = FakeContex([])
    baselines = Baselines(contex, config(tmp_path))
    path = tmp_path / "seed.json.baselines.json"
    await baselines.load(["a"])
    contex.scores = [0.5]
    await baselines.load(["b"])
    assert cached(path) == {"b": 0.5}
    await baselines.load(["a"])
    assert cached(path) == {"a": 0.5, "b": 0.5}


@pytest.mark.asyncio
async def test_set_is_published_once_per_instance(tmp_path):
    contex = FakeContex()
    baselines = Baselines(contex, config(tmp_path))
    await baselines.load(["a"])
    await baselines.load(["b"])
    assert len(contex.published) == len(CALIBRATION)
    assert [q[1] for q in contex.queries] == ["a", "b"]


@pytest.mark.asyncio
async def test_concurrent_loads_keep_both_texts_and_publish_once(tmp_path):
    contex = FakeContex()
    baselines = Baselines(contex, config(tmp_path))
    await asyncio.gather(baselines.load(["a"]), baselines.load(["b"]))
    assert len(contex.published) == len(CALIBRATION)
    assert set(cached(tmp_path / "seed.json.baselines.json")) == {"a", "b"}


def test_threshold_adds_margin(tmp_path):
    baselines = Baselines(FakeContex(), config(tmp_path, match_margin=0.05))
    baselines.set("a", 0.75)
    assert baselines.threshold("a") == pytest.approx(0.80)
    assert baselines.matches("a", 0.80)
    assert not baselines.matches("a", 0.79)


def test_explicit_calibration_path_wins(tmp_path):
    cfg = config(tmp_path, calibration_path=str(tmp_path / "x.json"))
    assert Baselines(FakeContex(), cfg).path == str(tmp_path / "x.json")


def test_playtest_fixture(tmp_path):
    baselines = Baselines(FakeContex(), config(tmp_path, match_margin=0.02))
    for need, base in [("curse", 0.769), ("vineyards", 0.742), ("cellar", 0.784), ("mistress", 0.784)]:
        baselines.set(need, base)
    assert baselines.matches("curse", 0.807)
    assert baselines.matches("vineyards", 0.780)
    assert not baselines.matches("cellar", 0.799)
    assert not baselines.matches("mistress", 0.786)


def test_default_margin_lets_the_playtest_tavern_whisper_reach_the_cellar(tmp_path):
    baselines = Baselines(FakeContex(), config(tmp_path))
    baselines.set("cellar", 0.784)
    assert baselines.matches("cellar", 0.799)


@pytest.mark.skipif(not URL, reason="RUMOR_MILL_CONTEX_URL not set")
@pytest.mark.asyncio
async def test_live_calibration_writes_cache(tmp_path):
    cfg = Config(contex_url=URL, session=f"cal-{os.urandom(4).hex()}", seed_path=str(tmp_path / "seed.json"))
    texts = ["Has anyone been saying my bread is stale?", "I dread omens and curses."]
    async with ContexClient(URL) as client:
        baselines = Baselines(client, cfg)
        await baselines.load(texts)
    for text in texts:
        assert 0.5 < baselines.get(text) < 0.95
    assert set(cached(tmp_path / "seed.json.baselines.json")) == set(texts)


@pytest.mark.asyncio
async def test_whisper_baseline_is_not_cached_to_disk(tmp_path):
    contex = FakeContex()
    baselines = Baselines(contex, config(tmp_path))
    assert await baselines.measure("the miller waters his flour") == pytest.approx(0.75)
    assert len(contex.published) == len(CALIBRATION)
    assert baselines.values == {}
    assert not (tmp_path / "seed.json.baselines.json").exists()
    await baselines.load(["a need"])
    assert set(cached(tmp_path / "seed.json.baselines.json")) == {"a need"}
    assert len(contex.published) == len(CALIBRATION)
