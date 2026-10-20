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


class StubBaselines:
    def __init__(self, baseline=0.7, margin=0.05):
        self.baseline, self.margin = baseline, margin

    def threshold(self, text):
        return self.baseline + self.margin


class StubContex:
    def __init__(self):
        self.minds: dict[str, list[Match]] = {}
        self.groups: dict[str, list[Match]] = {}
        self.thresholds: list[float | None] = []
        self.thresholds_by_project: dict[str, float | None] = {}
        self.max_ages: dict[str, int | None] = {}

    async def query(self, project_id, query, top_k=3, threshold=None, max_age_seconds=None):
        self.thresholds.append(threshold)
        self.thresholds_by_project[project_id] = threshold
        self.max_ages[project_id] = max_age_seconds
        found = self.minds.get(project_id) or self.groups.get(project_id) or []
        return [m for m in found if threshold is None or m.similarity is None or m.similarity >= threshold]


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

    def on_met(self, goal):
        self.said.append(f'Goal met: "{goal.text}"')


def make(n=4, believers=0):
    town, contex, jev, feed = Town(), StubContex(), StubJev(), Lines()
    for i in range(n):
        v = Villager(f"v{i}", f"Vi Name{i}", "baker", "calm", "none", [], {})
        town.add(v)
        if i < believers:
            contex.minds[mind_project(CONFIG.session, v.id)] = [Match("k", {"text": "the cat is a spy"})]
    return Judge(contex, jev, town, CONFIG, feed, baselines=StubBaselines()), contex, jev, feed, town


@pytest.mark.asyncio
async def test_villagers_without_matches_cost_no_jev_calls():
    judge, contex, jev, _, _ = make(n=4, believers=1)
    await judge.check(BELIEF)
    assert len(jev.calls) == 1
    assert contex.thresholds == [StubBaselines().threshold("")] * 4


@pytest.mark.asyncio
async def test_judge_queries_with_the_evidence_threshold():
    judge, contex, _, _, _ = make(n=2, believers=2)
    judge.baselines = StubBaselines(baseline=0.6, margin=0.1)
    await judge.check(BELIEF)
    assert contex.thresholds == [0.7, 0.7]


@pytest.mark.asyncio
async def test_villager_below_the_margin_costs_no_jev_call():
    judge, contex, jev, _, town = make(n=1)
    contex.minds[mind_project(CONFIG.session, "v0")] = [Match("k", {"text": "weather"}, similarity=0.74)]
    judge.baselines = StubBaselines(baseline=0.7, margin=0.05)
    assert await judge.check(BELIEF) == 0.0
    assert jev.calls == []


@pytest.mark.asyncio
async def test_mind_queries_ignore_forgotten_memories():
    judge, contex, _, _, _ = make(n=2, believers=2)
    g = judge.town.found_group("v0", "Order", "worship")
    judge.town.join("v1", g.id)
    creed = Goal("c", "religion", "belief", ("sim",), "q", 0.25, "creed?", 2)
    await judge.check(creed)
    assert contex.max_ages[mind_project(CONFIG.session, "v0")] == CONFIG.memory_seconds
    assert contex.max_ages[group_project(CONFIG.session, g.id)] is None


@pytest.mark.asyncio
async def test_group_query_leaves_the_decision_to_the_creed_question():
    judge, contex, _, _, _ = make(n=2, believers=2)
    judge.baselines = StubBaselines(baseline=0.6, margin=0.1)
    g = judge.town.found_group("v0", "Order", "worship")
    judge.town.join("v1", g.id)
    creed = Goal("c", "religion", "belief", ("sim",), "q", 0.25, "creed?", 2)
    await judge.check(creed)
    assert contex.thresholds_by_project[group_project(CONFIG.session, g.id)] == 0
    assert contex.thresholds_by_project[mind_project(CONFIG.session, "v0")] == 0.7


@pytest.mark.asyncio
async def test_unchanged_matches_are_not_judged_again():
    judge, _, jev, _, _ = make(n=3, believers=3)
    await judge.check(BELIEF)
    await judge.check(BELIEF)
    assert len(jev.calls) == 3


@pytest.mark.asyncio
async def test_changed_matches_are_judged_again():
    judge, contex, jev, _, _ = make(n=1, believers=1)
    await judge.check(BELIEF)
    contex.minds[mind_project(CONFIG.session, "v0")] = [
        Match("k", {"text": "the cat is a spy"}), Match("k2", {"text": "the cat sells secrets"}),
    ]
    await judge.check(BELIEF)
    assert len(jev.calls) == 2


@pytest.mark.asyncio
async def test_idle_village_progress_is_stable():
    judge, _, jev, _, _ = make(n=4, believers=4)
    first = await judge.check(BELIEF)
    jev.answer = 0.1
    assert await judge.check(BELIEF) == first == 1.0


@pytest.mark.asyncio
async def test_departed_villagers_are_evicted_from_the_cache():
    judge, _, _, _, town = make(n=2, believers=2)
    await judge.check(BELIEF)
    town.remove("v0")
    await judge.check(BELIEF)
    assert {vid for _, vid, _ in judge._verdicts} == {"v1"}


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
    assert judge.met == {BELIEF.id}


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
async def test_failing_villager_is_not_counted():
    judge, contex, _, _, _ = make(n=3, believers=1)
    healthy = contex.query

    async def query(project_id, *a, **k):
        if project_id == mind_project(CONFIG.session, "v1"):
            raise RuntimeError("down")
        return await healthy(project_id, *a, **k)

    contex.query = query
    assert await judge.check(BELIEF) == 1.0


@pytest.mark.asyncio
async def test_all_villagers_failing_gives_zero_progress():
    judge, contex, _, _, _ = make(n=2, believers=2)

    async def boom(*a, **k):
        raise RuntimeError("down")

    contex.query = boom
    assert await judge.check(BELIEF) == 0.0
