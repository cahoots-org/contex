import random
import re
from collections import Counter
from types import SimpleNamespace

import pytest

from examples.rumor_mill.goals import BANDS
from examples.rumor_mill.lineage import Ids
from examples.rumor_mill.models import Villager
from examples.rumor_mill.pool import Entry, Pool
from examples.rumor_mill.tests.helpers import StubBaselines, StubRoutine, goal_maker
from examples.rumor_mill.town import Town

AGNES = Villager("v1", "Agnes Thatch", "weaver", "nosy", "Hob let his pigs into her garden", [], {})
HOB = Villager("v2", "Hob Miller", "miller", "gruff", "", [], {})
COB = Villager("v3", "Cob Smith", "smith", "loud", "", [], {})
DOT = Villager("v4", "Dot Reed", "baker", "kind", "", [], {})
FARMERS = [Villager(f"v{i}", f"Farmer{i} Field", "farmer", "calm", "", [], {}) for i in (5, 6, 7)]
ALE = Pool((Entry("waters down the ale", 3),), (Entry("the well is cursed", 3),))
NO_CLAIMS = Pool((Entry("waters down the ale", 2), Entry("cheats at dice", 2)), ())
TAVERN = StubRoutine(work={v.id: "tavern" for v in (AGNES, *FARMERS)})


def town_of(*villagers):
    town = Town()
    for v in villagers:
        town.add(v)
    return town


class LoggingBaselines(StubBaselines):
    def __init__(self):
        super().__init__(0.6)
        self.loaded = []
        self.persisted = []

    async def load(self, texts, persist=True):
        self.loaded.extend(texts)
        self.persisted.append(persist)


class ChoosingJev:
    """Picks the criteria key whose description starts with a wanted text, else the first; records each request."""

    def __init__(self, *wanted):
        self.wanted = wanted
        self.requests = []

    async def system_one(self, state, questions):
        (key, question), = questions.items()
        self.requests.append((state, question))
        criteria = question.criteria
        pick = next((k for k, d in criteria.items() for w in self.wanted if str(d).startswith(w)), next(iter(criteria)))
        return SimpleNamespace(choices={key: SimpleNamespace(choice=pick)})


class BrokenJev:
    async def system_one(self, state, questions):
        raise RuntimeError("down")


PATTERNS = (
    r"Convince \w+ that \w+ waters down the ale",
    r"Get half of the tavern regulars to believe \w+ waters down the ale",
    r"Half of the village believes the well is cursed",
)


def test_draw_fills_personal_circle_and_village_templates():
    town = town_of(AGNES, HOB, *FARMERS)
    drawn = [g for seed in range(200) for g in goal_maker(town, ALE, TAVERN, rng=random.Random(seed)).draw("p-1", set(), 1)]
    by_text = {g.text: g for g in drawn}
    assert all(any(re.fullmatch(p, t) for p in PATTERNS) for t in by_text)
    personal = by_text["Convince Agnes that Hob waters down the ale"]
    assert personal.audience == (AGNES.id,) and personal.subject == HOB.id and personal.audience_label == "Agnes"
    assert personal.claim_text == "Hob waters down the ale" and personal.evidence == ("Hob waters down the ale",)
    assert personal.believer_question == "Does this villager believe Hob waters down the ale?"
    circle = by_text["Get half of the tavern regulars to believe Hob waters down the ale"]
    assert set(circle.audience) == {AGNES.id, *(f.id for f in FARMERS)} and circle.target_fraction == BANDS[3][0]
    assert circle.audience_label == "the tavern regulars" and circle.subject == HOB.id
    village = by_text["Half of the village believes the well is cursed"]
    assert village.audience == () and village.subject is None and village.claim_text == "the well is cursed"
    assert all(g.owner == "p-1" and g.id.startswith("gen-p-1-") and not g.long_shot for g in drawn)


def test_a_group_is_a_circle_labelled_by_its_name():
    town = town_of(AGNES, HOB, *FARMERS)
    group = town.found_group(FARMERS[0].id, "The Plough Guild", "fair prices")
    for f in FARMERS[1:] + [AGNES]:
        town.join(f.id, group.id)
    drawn = [g for seed in range(100) for g in goal_maker(town, ALE, rng=random.Random(seed)).draw("p-1", set(), 1)]
    labels = {g.audience_label for g in drawn if len(g.audience) > 1}
    assert labels == {"the Plough Guild"}


def test_a_long_shot_entry_makes_a_long_shot_goal():
    pool = Pool((Entry("waters down the ale", 0),), ())
    (goal,) = goal_maker(town_of(AGNES, HOB), pool).draw("p-1", set(), 1)
    assert goal.long_shot


@pytest.mark.asyncio
async def test_hand_skips_templates_that_cannot_be_filled():
    for seed in range(20):
        dealt = await goal_maker(town_of(AGNES, HOB), rng=random.Random(seed)).hand("p-1", set())
        assert len(dealt) == 3
        assert all(len(g.audience) <= 1 for g in dealt)
        assert sum(g.subject is not None for g in dealt) <= 2


@pytest.mark.asyncio
async def test_jev_failure_falls_back_to_uniform_fill():
    town = town_of(AGNES, HOB, *FARMERS)
    drawn = goal_maker(town, routine=TAVERN, rng=random.Random(3)).draw("p-1", set())
    dealt = await goal_maker(town, routine=TAVERN, jev=BrokenJev(), rng=random.Random(3)).hand("p-1", set())
    assert [g.text for g in dealt] == [g.text for g in drawn] and len(dealt) == 3


@pytest.mark.asyncio
async def test_hand_uses_jev_choices():
    jev = ChoosingJev("cheats at dice", "Cob Smith")
    maker = goal_maker(town_of(AGNES, HOB, COB), NO_CLAIMS, jev=jev)
    (goal,) = await maker.hand("p-1", set(), 1, subjects={AGNES.id, COB.id})
    assert goal.text == "Convince Cob that Hob cheats at dice" and goal.audience == (COB.id,)
    (accusation_state, _), (mark_state, mark_question) = jev.requests
    assert accusation_state["subject"]["name"] == "Hob Miller"
    assert accusation_state["subject"]["held_against_them"] == [AGNES.grudge]
    assert mark_state["rumor"] == "Hob cheats at dice" and HOB.id not in mark_question.criteria


def test_subject_weighting_prefers_grudge_targets():
    rival = Villager("v8", "Ned Rook", "carter", "sly", "", [], {"v4": "rival"})
    dot = Villager("v4", "Dot Reed", "baker", "kind", "", [], {"v8": "rival"})
    maker = goal_maker(town_of(AGNES, HOB, COB, dot, rival), NO_CLAIMS, rng=random.Random(7))
    counts = Counter(maker.draw("p-1", set(), 1)[0].subject for _ in range(1000))
    for target in (HOB.id, dot.id, rival.id):
        assert counts[target] > 2 * counts[COB.id]
        assert counts[target] > 2 * counts[AGNES.id]


@pytest.mark.asyncio
async def test_taken_accusations_not_offered():
    jev = ChoosingJev()
    maker = goal_maker(town_of(AGNES, HOB, COB), NO_CLAIMS, jev=jev)
    (goal,) = await maker.hand("p-1", {"hob waters down the ale"}, 1, subjects={AGNES.id, COB.id})
    (_, question), _ = jev.requests
    assert list(question.criteria.values()) == ["cheats at dice"]
    assert goal.claim_text == "Hob cheats at dice"


def test_a_subject_with_every_accusation_taken_is_not_drawn():
    maker = goal_maker(town_of(AGNES, HOB), Pool((Entry("waters down the ale", 2),), ()))
    for _ in range(20):
        (goal,) = maker.draw("p-1", {"hob waters down the ale"}, 1)
        assert goal.subject == AGNES.id


def test_subjects_never_repeat_in_a_hand():
    town = town_of(AGNES, HOB, COB, DOT)
    for seed in range(30):
        hand = goal_maker(town, NO_CLAIMS, rng=random.Random(seed)).draw("p-1", set())
        subjects = [g.subject for g in hand]
        assert len(hand) == 3 and len(set(subjects)) == 3


def test_regulars_exclude_square_and_gate_and_subject():
    regulars = (COB, DOT, *FARMERS)
    routine = StubRoutine(work={v.id: "mill" for v in regulars}, leisure={v.id: "gate" for v in regulars})
    routine.work |= {AGNES.id: "square", HOB.id: "mill"}
    town = town_of(AGNES, HOB, *regulars)
    drawn = [g for seed in range(100) for g in goal_maker(town, ALE, routine, rng=random.Random(seed)).draw("p-1", set(), 1)]
    circles = [g for g in drawn if len(g.audience) > 1]
    assert circles and {g.audience_label for g in circles} == {"the mill regulars"}
    assert all(g.subject not in g.audience for g in circles)


def test_a_circle_needs_three_besides_the_subject():
    routine = StubRoutine(work={v.id: "mill" for v in (AGNES, HOB, COB)})
    drawn = [g for seed in range(50) for g in goal_maker(town_of(AGNES, HOB, COB), ALE, routine, rng=random.Random(seed)).draw("p-1", set(), 1)]
    assert all(len(g.audience) <= 1 for g in drawn)


@pytest.mark.asyncio
async def test_hand_loads_baselines_and_publishes_evidence():
    baselines = LoggingBaselines()
    maker = goal_maker(town_of(AGNES, HOB, *FARMERS), baselines=baselines)
    dealt = await maker.hand("p-1", set())
    assert baselines.loaded == [g.claim_text for g in dealt] and baselines.persisted == [False]
    assert maker.overhear.generated_ids() == {g.id for g in dealt}


@pytest.mark.asyncio
async def test_drawn_goals_are_published_when_prepared():
    baselines = LoggingBaselines()
    maker = goal_maker(town_of(AGNES, HOB, *FARMERS), baselines=baselines)
    drawn = maker.draw("p-1", set())
    assert maker.overhear.generated_ids() == set()
    await maker.prepare()
    assert baselines.loaded == [g.claim_text for g in drawn]
    assert maker.overhear.generated_ids() == {g.id for g in drawn}
    await maker.prepare()
    assert len(baselines.loaded) == 3


@pytest.mark.asyncio
async def test_generated_ids_unique_across_players():
    maker = goal_maker(town_of(AGNES, HOB, *FARMERS), ids=Ids())
    first, second = await maker.hand("p-1", set()), await maker.hand("p-2", set())
    ids = [g.id for g in first + second]
    assert len(set(ids)) == 6 and all(i.startswith(("gen-p-1-", "gen-p-2-")) for i in ids)


def test_a_mark_sharing_a_first_name_is_called_by_full_name():
    town = town_of(AGNES, HOB, Villager("v9", "Agnes Reed", "baker", "kind", "", [], {}))
    drawn = [g for seed in range(50) for g in goal_maker(town, NO_CLAIMS, rng=random.Random(seed)).draw("p-1", set(), 1)]
    labels = {g.audience_label for g in drawn}
    assert labels <= {"Agnes Thatch", "Agnes Reed", "Hob"} and "Agnes Thatch" in labels
    assert all("Agnes " in g.claim_text for g in drawn if g.subject in (AGNES.id, "v9"))
