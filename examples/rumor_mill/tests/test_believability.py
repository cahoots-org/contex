from types import SimpleNamespace

import pytest

from examples.rumor_mill.believability import Believability


class ScoreJev:
    def __init__(self, *scores):
        self.scores = list(scores)
        self.calls = 0

    async def system_one(self, state, questions):
        self.calls += 1
        score = self.scores.pop(0)
        if isinstance(score, Exception):
            raise score
        return SimpleNamespace(scores={"q": SimpleNamespace(score=score, confidence=0.9)})


@pytest.mark.asyncio
async def test_band_maps_score_and_caches():
    jev = ScoreJev(0.1, 0.9)
    b = Believability(jev)
    assert await b.band("dragons") == 0
    assert await b.band("dragons") == 0
    assert jev.calls == 1
    assert await b.band("rain") == 3
    assert await Believability(ScoreJev(1.0)).band("x") == 3


@pytest.mark.asyncio
async def test_band_failure_is_middling_and_uncached():
    jev = ScoreJev(RuntimeError("down"), 0.3)
    b = Believability(jev)
    assert await b.band("dragons") == 2
    assert await b.band("dragons") == 1
    assert jev.calls == 2
