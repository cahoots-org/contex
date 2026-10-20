import asyncio
from types import SimpleNamespace

import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.judge import Judge
from examples.rumor_mill.mind import group_project, mind_project
from examples.rumor_mill.models import Villager
from examples.rumor_mill.town import Town

CONFIG = Config(day_seconds=0.01, judge_every_days=1.0)
BELIEF = Goal("g", "cat is a spy", "belief", ("cat spy",), "Does it believe?", 0.5, hold_checks=2)


class StubContex:
    def __init__(self):
        self.minds: dict[str, list[Match]] = {}
        self.groups: dict[str, list[Match]] = {}
        self.thresholds: list[float | None] = []

    async def query(self, project_id, query, top_k=3, threshold=None):
        self.thresholds.append(threshold)
        return self.minds.get(project_id) or self.groups.get(project_id) or []


class StubJev:
    def __init__(self, answer=0.9):
        self.answer = answer
        self.calls: list[tuple[dict, dict]] = []

    async def system_one(self, state, questions):
        self.calls.append((state, questions))
        return SimpleNamespace(nouls={k: SimpleNamespace(noul=self.answer) for k in questions})


class Lines:
    def __init__(self):
        self.progress: list[tuple[str, float]] = []
        self.said: list[str] = []

    def on_progress(self, goal, fraction):
        self.progress.append((goal.id, fraction))

    def say(self, line):
        self.said.append(line)


def make(n=4, believers=0):
    town, contex, jev, feed = Town(), StubContex(), StubJev(), Lines()
    for i in range(n):
        v = Villager(f"v{i}", f"Vi Name{i}", "baker", "calm", "none", [], {})
        town.add(v)
        if i < believers:
            contex.minds[mind_project(CONFIG.session, v.id)] = [Match("k", {"text": "the cat is a spy"})]
    return Judge(contex, jev, town, CONFIG, feed), contex, jev, feed, town


@pytest.mark.asyncio
async def test_villagers_without_matches_cost_no_jev_calls():
    judge, contex, jev, _, _ = make(n=4, believers=1)
    await judge.check(BELIEF)
    assert len(jev.calls) == 1
    assert contex.thresholds == [CONFIG.judge_threshold] * 4


@pytest.mark.asyncio
async def test_unlikely_yes_does_not_count_as_belief():
    judge, _, jev, _, _ = make(n=4, believers=4)
    jev.answer = 0.3
    assert await judge.check(BELIEF) == 0.0


@pytest.mark.asyncio
async def test_progress_is_believer_fraction_over_target():
    judge, *_ = make(n=4, believers=1)
    assert await judge.check(BELIEF) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_goal_met_only_after_hold_checks_consecutive_checks():
    judge, _, _, feed, town = make(n=2, believers=2)
    progress = iter([1.0, 0.5, 1.0, 1.0])

    async def check(goal):
        return next(progress)

    judge.check = check
    await asyncio.wait_for(judge.run([BELIEF]), 2)
    assert next(progress, None) is None
    assert feed.said == ['Goal met: "cat is a spy"']


@pytest.mark.asyncio
async def test_creed_goal_capped_without_qualifying_group():
    judge, contex, jev, _, town = make(n=4, believers=4)
    creed = Goal("c", "religion", "belief", ("sim",), "q", 0.25, "creed?", 2)
    for i in range(4):
        contex.minds[mind_project(CONFIG.session, f"v{i}")] = [Match("k", {"text": "sim"})]
    assert await judge.check(creed) < 1.0
    g = town.found_group("v0", "Order", "worship")
    town.join("v1", g.id)
    contex.groups[group_project(CONFIG.session, g.id)] = [Match(f"group:{g.id}", {"name": "Order"})]
    assert await judge.check(creed) == 1.0
    jev.answer = 0.1
    assert await judge.check(creed) < 1.0


@pytest.mark.asyncio
async def test_state_goal_uses_town_departures():
    judge, _, _, _, town = make(n=4)
    goal = Goal("f", "3 families leave", "state", state_check=lambda t: min(1.0, len({v.name.split()[-1] for v in t.departed}) / 3))
    assert await judge.check(goal) == 0.0
    for vid in ("v0", "v1", "v2"):
        town.remove(vid)
    assert await judge.check(goal) == 1.0


@pytest.mark.asyncio
async def test_progress_line_only_when_percent_changes():
    judge, _, _, feed, _ = make()
    progress = iter([0.5, 0.5, 0.504, 0.6, 1.0, 1.0, 1.0])

    async def check(goal):
        return next(progress)

    judge.check = check
    await asyncio.wait_for(judge.run([BELIEF]), 2)
    assert [round(f, 2) for _, f in feed.progress] == [0.5, 0.6, 1.0]


@pytest.mark.asyncio
async def test_failing_villager_counts_as_non_believer():
    judge, contex, _, _, _ = make(n=2, believers=2)

    async def boom(*a, **k):
        raise RuntimeError("down")

    contex.query = boom
    assert await judge.check(BELIEF) == 0.0
