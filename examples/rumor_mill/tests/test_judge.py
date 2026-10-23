import asyncio
import math
import random
from types import SimpleNamespace

import pytest

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.game import Game
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.goals import DECK, WORLD_CANDIDATES, Goal, Trophy, void_reason, world_goal
from examples.rumor_mill.judge import Judge
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.mind import group_project, mind_project
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.overhear import Overhear
from examples.rumor_mill.players import Players
from examples.rumor_mill.tests.helpers import PID, wait_for
from examples.rumor_mill.tests.helpers import StubContex as OverhearContex
from examples.rumor_mill.town import Town

CONFIG = Config(day_seconds=0.01, judge_every_days=1.0)
BELIEF = Goal("g", "cat is a spy", "belief", ("cat spy",), "Does it believe?", 0.5, hold_checks=2)
QUIET = Goal("q", "nobody leaves", "state", state_check=lambda town: 0.0)


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
        self.sinces: dict[str, str | None] = {}

    async def query(self, project_id, query, top_k=3, threshold=None, max_age_seconds=None, since=None):
        self.thresholds.append(threshold)
        self.thresholds_by_project[project_id] = threshold
        self.sinces[project_id] = since
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
        self.progress: list[tuple[str, float, str | None]] = []
        self.met: list[tuple[str, str | None]] = []
        self.deals: list[tuple[list[str], str]] = []
        self.worlds: list[str] = []
        self.trophies: list[tuple[Trophy, str, str]] = []
        self.rumors: list[tuple[str | None, str | None]] = []
        self.doubts: list[tuple[str | None, dict | None]] = []

    def on_progress(self, goal, fraction, counts=None, player=None, rumor=None, doubt=None):
        self.progress.append((goal.id, fraction, player))
        self.rumors.append((player, rumor))
        self.doubts.append((player, doubt))

    def on_met(self, goal, player=None):
        self.met.append((goal.id, player))

    def on_deal(self, goals, player, counts=None):
        self.deals.append(([g.id for g in goals], player))
        self.deal_counts = counts

    def on_world(self, goal, counts=None):
        self.worlds.append(goal.id)

    def on_trophy(self, trophy, line, player):
        self.trophies.append((trophy, line, player))


def holder(pid, *goals, whispered_at=None, over=False):
    return SimpleNamespace(
        id=pid, owner=f"o-{pid}", goals=list(goals), met=set(), whispered_at=whispered_at, over=lambda: over,
    )


def players(*ps, feed=None):
    found = Players(feed)
    for p in ps:
        found.add(p)
    return found


def scripted(judge, values, goal=BELIEF):
    """Make judge.check answer the goal from values in turn, and every other goal with 0."""
    values = iter(values)

    async def check(g):
        return next(values) if g.id == goal.id else 0.0

    judge.check = check
    return values


async def run_until(judge, everyone, done):
    running = asyncio.create_task(judge.run(everyone))
    try:
        await wait_for(done)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


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
async def test_mind_queries_ignore_lines_older_than_the_remembered_moves():
    judge, contex, _, _, _ = make(n=2, believers=2)
    judge.game = Game(memory_moves=2)
    g = judge.town.found_group("v0", "Order", "worship")
    judge.town.join("v1", g.id)
    creed = Goal("c", "religion", "belief", ("sim",), "q", 0.25, "creed?", 2)
    await judge.check(creed)
    assert contex.sinces[mind_project(CONFIG.session, "v0")] is None
    for _ in range(3):
        judge.game.moved(PID)
    await judge.check(creed)
    assert contex.sinces[mind_project(CONFIG.session, "v0")] == judge.game.memory_since() is not None
    assert contex.sinces[group_project(CONFIG.session, g.id)] is None


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


class ClaimJev(StubJev):
    """Says yes only to memories that state the claim."""

    async def system_one(self, state, questions):
        self.answer = 0.9 if any("spy" in m for m in state["memories"]) else 0.2
        return await super().system_one(state, questions)


@pytest.mark.asyncio
async def test_doubters_are_counted_with_what_the_closest_one_is_saying():
    judge, contex, _, _, _ = make(n=4, believers=1)
    judge.jev = jev = ClaimJev()
    contex.minds[mind_project(CONFIG.session, "v1")] = [Match("a", {"text": "the cat is odd"}, 0.8)]
    contex.minds[mind_project(CONFIG.session, "v2")] = [
        Match("b", {"text": "the cat is a thief"}, 0.85), Match("c", {"text": "cats"}, 0.8),
    ]
    await judge.check(BELIEF)
    assert judge.doubt[BELIEF.id] == {"count": 2, "saying": "the cat is a thief"}
    assert len(jev.calls) == 3 and len(contex.thresholds) == 4


@pytest.mark.asyncio
async def test_a_players_whisper_counts_as_heard_but_is_never_the_saying():
    _, contex, _, feed, town = make(n=2)
    lineage = Lineage()
    lineage.record("w-1", None, "p-1")
    judge = Judge(contex, StubJev(0.2), town, CONFIG, feed, baselines=StubBaselines(), lineage=lineage)
    whisper = Match("utt:w-1", {"id": "w-1", "text": "A newcomer whispered to you: the cat sells our secrets"}, 0.95)
    contex.minds[mind_project(CONFIG.session, "v0")] = [whisper, Match("u", {"id": "u-2", "text": "the cat is odd"}, 0.8)]
    contex.minds[mind_project(CONFIG.session, "v1")] = [whisper]
    await judge.check(BELIEF)
    assert judge.doubt[BELIEF.id] == {"count": 2, "saying": "the cat is odd"}


@pytest.mark.asyncio
async def test_the_saying_is_the_line_as_the_game_kept_it():
    _, contex, _, feed, town = make(n=1)
    facts = Facts()
    sermon = Utterance("u-3", "v9", "Wren Crane", "chapel", "the cat: a devil in fur", None, "preached")
    facts.record(sermon)
    judge = Judge(contex, StubJev(0.2), town, CONFIG, feed, baselines=StubBaselines(), facts=facts)
    contex.minds[mind_project(CONFIG.session, "v0")] = [Match("utt:u-3", sermon.to_data(), 0.9)]
    await judge.check(BELIEF)
    assert judge.doubt[BELIEF.id]["saying"] == "the cat: a devil in fur"


@pytest.mark.asyncio
async def test_a_new_saying_alone_sends_no_progress():
    judge, contex, jev, feed, _ = make(n=2, believers=2)
    judge.world = QUIET
    jev.answer = 0.2
    running = asyncio.create_task(judge.run(players(holder(PID, BELIEF))))
    try:
        await wait_for(lambda: feed.doubts)
        contex.minds[mind_project(CONFIG.session, "v0")] = [Match("k2", {"text": "the cat is odd"}, 0.99)]
        await wait_for(lambda: judge.doubt[BELIEF.id]["saying"] == "the cat is odd")
        assert len(feed.doubts) == 1
        del contex.minds[mind_project(CONFIG.session, "v0")]
        await wait_for(lambda: len(feed.doubts) == 2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert feed.doubts[1] == (PID, {"count": 1, "saying": "the cat is a spy"})


@pytest.mark.asyncio
async def test_no_doubters_means_no_saying():
    judge, *_ = make(n=2, believers=1)
    await judge.check(BELIEF)
    assert judge.doubt[BELIEF.id] == {"count": 0, "saying": None}


@pytest.mark.asyncio
async def test_progress_carries_the_doubt_and_doubt_is_forgotten_with_the_goal():
    judge, _, jev, feed, _ = make(n=2, believers=2)
    judge.world = QUIET
    jev.answer = 0.2
    p = holder(PID, BELIEF)
    running = asyncio.create_task(judge.run(players(p)))
    try:
        await wait_for(lambda: (PID, {"count": 2, "saying": "the cat is a spy"}) in feed.doubts)
        p.goals = []
        await wait_for(lambda: BELIEF.id not in judge.doubt)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_progress_is_believer_fraction_over_target():
    judge, *_ = make(n=4, believers=1)
    assert await judge.check(BELIEF) == pytest.approx(0.5)
    assert judge.counts["g"] == (1, 2)


@pytest.mark.asyncio
async def test_a_belief_goal_counts_against_the_village_before_its_first_check():
    judge, *_ = make(n=4, believers=1)
    assert judge.counts_of(BELIEF) == (0, 2)
    assert judge.counts_of(QUIET) is None
    await judge.check(BELIEF)
    assert judge.counts_of(BELIEF) == (1, 2)


def test_a_deal_sends_counts_for_its_belief_goals_only():
    judge, _, _, feed, _ = make(n=4)
    p = SimpleNamespace(id=PID, goals=[], met=set())
    judge.deal(p)
    assert feed.deal_counts == {g.id: (0, math.ceil(g.target_fraction * 4)) for g in p.goals if g.kind == "belief"}


def test_an_empty_town_sends_no_counts_rather_than_none_of_none():
    judge, _, _, feed, _ = make(n=0)
    assert judge.counts_of(BELIEF) is None
    judge.deal(SimpleNamespace(id=PID, goals=[], met=set()))
    assert feed.deal_counts == {}


@pytest.mark.asyncio
async def test_needed_rounds_up_to_a_whole_villager():
    judge, *_ = make(n=50, believers=15)
    third = Goal("t", "tavern haunted", "belief", ("ghost",), "Does it believe?", 0.33)
    await judge.check(third)
    assert judge.counts["t"] == (15, 17)


@pytest.mark.asyncio
async def test_goal_met_only_after_hold_checks_consecutive_checks():
    judge, _, _, feed, _ = make(n=2, believers=2)
    values = scripted(judge, [1.0, 0.5, 1.0, 1.0])
    p = holder(PID, BELIEF, QUIET)
    await run_until(judge, players(p), lambda: feed.met)
    assert next(values, None) is None
    assert feed.met == [("g", PID)]
    assert p.met == {BELIEF.id}


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
    scripted(judge, [0.5, 0.5, 0.504, 0.6, 1.0, 1.0, 1.0])
    await run_until(judge, players(holder(PID, BELIEF)), lambda: feed.met)
    assert [(round(f, 2), p) for g, f, p in feed.progress if g == "g"] == [(0.5, PID), (0.6, PID), (1.0, PID)]


@pytest.mark.asyncio
async def test_one_check_per_claim_whoever_holds_it():
    judge, _, jev, feed, _ = make(n=4, believers=3)
    judge.world = QUIET
    await run_until(judge, players(holder("p-a", BELIEF), holder("p-b", BELIEF)), lambda: len(feed.progress) >= 2)
    assert len(jev.calls) == 3


@pytest.mark.asyncio
async def test_a_claim_met_is_met_for_every_player_holding_it():
    judge, _, _, feed, _ = make()
    scripted(judge, [1.0] * 10)
    pa, pb = holder("p-a", BELIEF, QUIET), holder("p-b", BELIEF, QUIET)
    await run_until(judge, players(pa, pb), lambda: len(feed.met) >= 2)
    assert pa.met == pb.met == {"g"} and [(g, p) for g, p in feed.met] == [("g", "p-a"), ("g", "p-b")]


@pytest.mark.asyncio
async def test_all_goals_met_deals_a_new_hand_and_the_game_goes_on():
    judge, _, _, feed, _ = make()
    judge.world = QUIET
    scripted(judge, [1.0] * 10)
    first_hand = [BELIEF]
    pa = holder("p-a", *first_hand)
    await run_until(judge, players(pa, feed=feed), lambda: feed.deals)
    assert pa.goals != first_hand and pa.met == set() and not pa.over()
    assert feed.deals == [([g.id for g in pa.goals], "p-a")]


@pytest.mark.asyncio
async def test_world_goal_met_draws_a_new_one():
    judge, _, _, feed, _ = make()
    old = judge.world
    scripted(judge, [1.0] * 10, goal=old)
    await run_until(judge, players(holder(PID)), lambda: feed.worlds)
    assert judge.world.id != old.id and feed.worlds == [judge.world.id]
    assert (old.id, None) in feed.met


def test_world_goal_falls_back_when_every_claim_is_held():
    judge, *_ = make()
    judge.progress = {g.id: 1.0 for g in WORLD_CANDIDATES}
    assert judge._next_world().id.startswith("world-")


def test_deal_skips_the_world_claim_and_held_claims():
    for seed in range(20):
        judge, *_ = make()
        judge.rng.seed(seed)
        judge.progress = {g.id: 1.0 for g in WORLD_CANDIDATES[:5]}
        pa = holder("p-a")
        judge.deal(pa)
        dealt = {g.id for g in pa.goals}
        assert judge.world.id.removeprefix("world-") not in dealt
        assert not dealt & {g.id for g in WORLD_CANDIDATES[:5]}


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


def test_deal_never_gives_the_world_claim_even_when_most_of_the_deck_is_held():
    judge, *_ = make()
    world_claim = judge.world.id.removeprefix("world-")
    judge.progress = {g.id: 1.0 for g in DECK[:-2]}
    for seed in range(50):
        judge.rng.seed(seed)
        pa = holder("p-a")
        judge.deal(pa)
        assert world_claim not in {g.id for g in pa.goals}


@pytest.mark.asyncio
async def test_a_claim_dealt_again_needs_hold_checks_again():
    judge, _, _, feed, _ = make()
    judge.world = QUIET
    checks = []

    async def check(goal):
        if goal.id != BELIEF.id:
            return 0.0
        checks.append(goal.id)
        return 1.0

    judge.check = check
    p = holder(PID, BELIEF, QUIET)
    running = asyncio.create_task(judge.run(players(p)))
    try:
        await wait_for(lambda: feed.met)
        p.goals = [QUIET]
        await wait_for(lambda: BELIEF.id not in judge.progress)
        p.goals, p.met = [BELIEF, QUIET], set()
        before = len(checks)
        await wait_for(lambda: len(feed.met) == 2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert len(checks) - before == BELIEF.hold_checks


SPY = Goal("cat", "Half the village believes cat is a spy", "belief", ("cat spy",), "q", 0.5, hold_checks=1)


def world_met(judge):
    """Make every world goal met, and every other goal unmet."""
    async def check(goal):
        return 1.0 if goal.id.startswith("world-") else 0.0

    judge.check = check


@pytest.mark.asyncio
async def test_meeting_every_goal_puts_a_trophy_in_the_case():
    judge, _, _, feed, _ = make()
    judge.world, judge.game = QUIET, Game()
    judge.game.day = 3
    scripted(judge, [1.0] * 10, goal=SPY)
    first_hand = [SPY]
    pa = holder("p-a", *first_hand)
    everyone = players(pa, feed=feed)
    await run_until(judge, everyone, lambda: feed.trophies)
    [t] = everyone.cases[pa.owner]
    assert (t.kind, t.goals, t.day) == ("personal", tuple(g.text for g in first_hand), judge.game.day)
    assert feed.trophies[0][1:] == (
        "The village now swears cat is a spy, and it all began with a whisper. A trophy for your case.", "p-a",
    )


@pytest.mark.asyncio
async def test_world_trophies_go_to_everyone_who_whispered_under_that_world_goal():
    judge, _, _, feed, _ = make()
    world_met(judge)
    pa = holder("p-a", whispered_at=judge.world_since + 1)
    pb = holder("p-b", whispered_at=judge.world_since - 1)
    pc = holder("p-c")
    everyone = players(pa, pb, pc, feed=feed)
    await run_until(judge, everyone, lambda: feed.worlds)
    assert [p.id for p in (pa, pb, pc) if everyone.cases.get(p.owner)] == ["p-a"]
    assert everyone.cases[pa.owner][0].kind == "world"
    assert feed.trophies[0][2] == "p-a" and feed.trophies[0][1].startswith("The whole village now holds that ")


@pytest.mark.asyncio
async def test_a_world_trophy_waits_for_a_run_out_persons_next_run():
    feed = Feed(Lineage(), Town(), out=None)
    judge = Judge(StubContex(), StubJev(), Town(), CONFIG, feed, baselines=StubBaselines())
    world_met(judge)
    pa = holder("p-a", whispered_at=judge.world_since + 1, over=True)
    feed.close(pa.id)
    everyone = players(pa, holder("p-b"), feed=feed)
    await run_until(judge, everyone, lambda: everyone.pending)
    assert everyone.pending[pa.owner] and feed.replay(pa.id) == []
    fresh = holder("p-a2")
    fresh.owner = pa.owner
    everyone.add(fresh)
    everyone.deliver_pending(fresh)
    assert [e.kind for e in feed.replay(fresh.id)][-1] == "trophy" and not everyone.pending.get(pa.owner)


@pytest.mark.asyncio
async def test_a_whisper_under_the_next_world_goal_does_not_count_for_the_last():
    judge, _, _, feed, _ = make()
    world_met(judge)
    pa = holder("p-a", whispered_at=judge.world_since)
    everyone = players(pa, feed=feed)
    await run_until(judge, everyone, lambda: len(feed.worlds) >= 2)
    assert [t.kind for t in everyone.cases[pa.owner]] == ["world"]


@pytest.mark.asyncio
async def test_no_goal_is_checked_while_nobody_is_playing():
    judge, _, _, _, _ = make()
    checked = []

    async def check(goal):
        checked.append(goal.id)
        return 0.0

    judge.check = check
    running = asyncio.create_task(judge.run(players(holder(PID, BELIEF, over=True))))
    await asyncio.sleep(10 * CONFIG.judge_every_days * CONFIG.day_seconds)
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert checked == []


@pytest.mark.asyncio
async def test_believers_are_traced_to_the_rumor_behind_their_best_memory():
    _, contex, jev, feed, town = make(n=3)
    lineage = Lineage()
    judge = Judge(contex, jev, town, CONFIG, feed, baselines=StubBaselines(), lineage=lineage)
    lineage.record("w-1", None, "p-1")
    lineage.record("u-1", "w-1")
    lineage.record("u-2", None)
    contex.minds[mind_project(CONFIG.session, "v0")] = [
        Match("utt:u-2", {"text": "a"}, 0.8), Match("utt:u-1", {"text": "b"}, 0.9),
    ]
    contex.minds[mind_project(CONFIG.session, "v1")] = [Match("utt:u-2", {"text": "a"}, 0.8)]
    await judge.check(BELIEF)
    assert judge.roots[BELIEF.id] == {"v0": "w-1", "v1": None}


@pytest.mark.asyncio
async def test_progress_names_the_players_rumor_that_moved_it():
    judge, _, _, feed, _ = make()
    judge.world = QUIET
    judge.lineage = lineage = Lineage()
    lineage.record("w-1", None, "p-1")
    lineage.record("w-2", None, "p-2")
    gains = iter([{"v0": "w-1"}, {"v0": "w-1", "v1": "w-1", "v2": "w-2"}])

    async def check(goal):
        if goal.id != BELIEF.id:
            return 0.0
        judge.roots[goal.id] = next(gains)
        return len(judge.roots[goal.id]) / 4

    judge.check = check
    await run_until(judge, players(holder("p-1", BELIEF), holder("p-2", BELIEF)), lambda: len(feed.rumors) >= 4)
    assert feed.rumors[:4] == [("p-1", "w-1"), ("p-2", None), ("p-1", "w-1"), ("p-2", "w-2")]


class BlockedContex(StubContex):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def query(self, *args, **kwargs):
        await self.release.wait()
        return await super().query(*args, **kwargs)


@pytest.mark.asyncio
async def test_roots_keep_the_last_complete_map_while_a_check_runs():
    _, _, jev, feed, town = make(n=1)
    contex = BlockedContex()
    lineage = Lineage()
    lineage.record("w-1", None, "p-1")
    contex.minds[mind_project(CONFIG.session, "v0")] = [Match("utt:w-1", {"text": "a"}, 0.9)]
    judge = Judge(contex, jev, town, CONFIG, feed, baselines=StubBaselines(), lineage=lineage)
    judge.roots[BELIEF.id] = {"v0": None}
    checking = asyncio.create_task(judge.check(BELIEF))
    await asyncio.sleep(0.01)
    assert judge.roots[BELIEF.id] == {"v0": None}
    contex.release.set()
    await checking
    assert judge.roots[BELIEF.id] == {"v0": "w-1"}


def audience_goal(*audience, fraction=0.5, **kw):
    return Goal("t", "they believe", "belief", ("cat spy",), "Does it believe?", fraction, audience=audience, **kw)


@pytest.mark.asyncio
async def test_audience_goal_counts_only_its_audience():
    judge, contex, _, _, _ = make(n=4)
    for vid in ("v0", "v2"):
        contex.minds[mind_project(CONFIG.session, vid)] = [Match("k", {"text": "the cat is a spy"})]
    goal = audience_goal("v0", "v1", audience_label="the bakers")
    assert await judge.check(goal) == 1.0
    assert judge.counts[goal.id] == (1, 1)
    assert list(judge.roots[goal.id]) == ["v0"]


@pytest.mark.asyncio
async def test_personal_goal_needs_one():
    judge, contex, _, _, _ = make(n=4)
    goal = audience_goal("v0", fraction=0.0)
    assert await judge.check(goal) == 0.0 and judge.counts[goal.id] == (0, 1)
    contex.minds[mind_project(CONFIG.session, "v0")] = [Match("k", {"text": "the cat is a spy"})]
    assert await judge.check(goal) == 1.0


@pytest.mark.asyncio
async def test_wary_mark_not_counted_for_owner():
    judge, contex, _, _, _ = make(n=2, believers=2)
    scores = {("v0", "p-1"): CONFIG.warn_threshold}
    judge.engine = SimpleNamespace(suspicion=SimpleNamespace(score=lambda vid, pid: scores.get((vid, pid), 0.0)))
    goal = audience_goal("v0", fraction=0.0, owner="p-1")
    assert await judge.check(goal) == 0.0
    assert judge.counts[goal.id] == (0, 1)
    assert judge.doubt[goal.id]["count"] == 1 and judge.roots[goal.id] == {}


def test_void_reason_cases():
    town = Town()
    for i in range(4):
        town.add(Villager(f"v{i}", f"Vi Name{i}", "baker", "calm", "none", [], {}))
    circle = audience_goal("v0", "v1", "v2", audience_label="the bakers")
    about = audience_goal(subject="v3")
    assert void_reason(circle, town) is None and void_reason(about, town) is None
    town.remove("v3")
    assert void_reason(about, town) == "Vi Name3 has left town"
    town.remove("v2")
    assert void_reason(circle, town) == "too few of the bakers are left"
    town.remove("v0")
    assert void_reason(audience_goal("v0"), town) == "Vi Name0 has left town"


@pytest.mark.asyncio
async def test_void_goal_replaced_when_mark_leaves():
    judge, _, _, feed, town = make(n=4)
    feed.voids = []
    feed.on_goal_void = lambda old, new, reason, player: feed.voids.append((old.id, new.id, reason, player))
    mark = audience_goal("v0", fraction=0.0)
    other = holder("p-2", BELIEF)
    mine = holder(PID, mark, QUIET, BELIEF)
    town.remove("v0")
    scripted(judge, [0.0] * 50)
    await run_until(judge, players(mine, other), lambda: feed.voids)
    assert len(mine.goals) == 3 and mark.id not in [g.id for g in mine.goals]
    assert [g.id for g in other.goals] == [BELIEF.id]
    assert feed.voids == [(mark.id, mine.goals[0].id, "Vi Name0 has left town", PID)]


class GatedMaker:
    def __init__(self, overhear=None):
        self.ready = asyncio.Event()
        self.overhear = overhear or SimpleNamespace(drop=lambda ids: None)
        self.calls = []

    async def hand(self, player_id, taken, deck_skip, never):
        self.calls.append((player_id, taken, deck_skip, never))
        await self.ready.wait()
        return [audience_goal("v0", fraction=0.0, owner=player_id), BELIEF]


@pytest.mark.asyncio
async def test_redeal_runs_in_background_and_keeps_old_hand_until_ready():
    judge, _, _, feed, _ = make()
    judge.world = world_goal(Goal("g", "cat", "belief", ("cat spy",), claim_text="The cat is a spy"))
    judge.maker = GatedMaker()
    scripted(judge, [1.0] * 100, goal=QUIET)
    theirs = Goal("theirs", "geese", "belief", ("geese",), claim_text="Hob steals geese", owner="p-b")
    pa, pb = holder("p-a", QUIET), holder("p-b", theirs)
    running = asyncio.create_task(judge.run(players(pa, pb, feed=feed)))
    try:
        await wait_for(lambda: judge.maker.calls)
        await asyncio.sleep(CONFIG.day_seconds * 3)
        assert pa.goals == [QUIET] and pa.met == {"q"} and feed.deals == []
        assert len(judge.maker.calls) == 1
        assert judge.maker.calls[0] == ("p-a", {"the cat is a spy", "hob steals geese"}, {"q"}, {"g"})
        judge.maker.ready.set()
        await wait_for(lambda: feed.deals)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert [g.id for g in pa.goals] == ["t", "g"] and pa.met == set()
    assert feed.deals == [(["t", "g"], "p-a")]


@pytest.mark.asyncio
async def test_forget_drops_generated_evidence():
    overhear = Overhear(OverhearContex(), StubBaselines(), CONFIG, random.Random(1))
    gen = Goal("gen-p-1-goal-1", "they believe", "belief", ("hob steals geese",), owner="p-1")
    await overhear.publish([gen, BELIEF])
    judge, _, _, _, _ = make()
    judge.maker = GatedMaker(overhear)
    judge.progress = {gen.id: 0.5, BELIEF.id: 0.5}
    judge._forget({BELIEF.id}, {})
    assert overhear._evidence == {f"goal:{BELIEF.id}": "cat spy"}


async def pending_redeal(judge, pa, feed, town=None):
    """Run the judge until pa's finished hand is waiting on a gated maker; returns the running task."""
    judge.world = QUIET
    judge.maker = GatedMaker()

    async def check(g):
        return 1.0 if g.id != QUIET.id else 0.0

    judge.check = check
    running = asyncio.create_task(judge.run(players(pa, feed=feed)))
    await wait_for(lambda: judge.maker.calls)
    return running


@pytest.mark.asyncio
async def test_no_void_line_for_a_won_hand_while_its_redeal_is_pending():
    judge, _, _, feed, town = make()
    feed.voids = []
    feed.on_goal_void = lambda old, new, reason, player: feed.voids.append(old.id)
    mark = audience_goal("v0", fraction=0.0, owner="p-a")
    pa = holder("p-a", mark)
    running = await pending_redeal(judge, pa, feed)
    try:
        town.remove("v0")
        await asyncio.sleep(CONFIG.day_seconds * 5)
        assert feed.voids == [] and pa.goals == [mark]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_no_second_trophy_while_a_redeal_is_pending():
    judge, _, _, feed, _ = make()
    pa = holder("p-a", BELIEF)
    running = await pending_redeal(judge, pa, feed)
    try:
        pa.goals = [Goal("swapped", "swapped", "belief", ("x",), "?", 0.5, hold_checks=1)]
        await asyncio.sleep(CONFIG.day_seconds * 5)
        assert len(feed.trophies) == 1 and "swapped" not in pa.met
        assert [g for g, _ in feed.met] == ["g"]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_hand_written_after_the_game_ended_is_dropped():
    judge, _, _, feed, _ = make()
    dropped = []
    judge.maker = GatedMaker(SimpleNamespace(drop=dropped.extend))
    judge.maker.ready.set()
    pa = holder("p-a", BELIEF, over=True)
    await judge.redeal(pa)
    assert pa.goals == [BELIEF] and feed.deals == [] and dropped == ["t"]


def traced_judge(roots):
    """A judge whose subject v1 remembers each uid in roots; lineage w1 is p-1's whisper, w2 p-2's."""
    judge, contex, _, _, _ = make(n=3)
    lineage = Lineage()
    lineage.record("w1", None, player="p-1")
    lineage.record("w2", None, player="p-2")
    lineage.record("u1", "w1")
    judge.lineage = lineage
    seen = []
    judge.engine = SimpleNamespace(
        suspicion=SimpleNamespace(score=lambda vid, pid: 0.0), traced=lambda *args: seen.append(args[1:]),
    )
    contex.minds[mind_project(CONFIG.session, "v1")] = [Match(f"utt:{uid}", {"id": uid}) for uid in roots]
    return judge, seen


@pytest.mark.asyncio
async def test_subject_remembering_the_owners_rumor_is_traced():
    judge, seen = traced_judge(["u1", "w2"])
    goal = audience_goal("v0", fraction=0.0, owner="p-1", subject="v1")
    await judge.check(goal)
    assert seen == [("v1", "u1")]


@pytest.mark.asyncio
async def test_subject_trace_makes_no_jev_call_and_skips_goals_without_owner_or_subject():
    judge, seen = traced_judge(["w1"])
    await judge.check(audience_goal("v0", fraction=0.0, owner="p-1", subject="v1"))
    assert seen == [("v1", "w1")] and judge.jev.calls == []
    await judge.check(audience_goal("v0", fraction=0.0, owner="p-1"))
    await judge.check(audience_goal("v0", fraction=0.0, subject="v1"))
    assert len(seen) == 1
