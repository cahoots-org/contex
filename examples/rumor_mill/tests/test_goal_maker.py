import random
import re
from collections import Counter
from types import SimpleNamespace

import pytest

from examples.rumor_mill import goal_maker as goal_maker_mod
from examples.rumor_mill.goals import BANDS, WORLD_FRACTION
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


@pytest.fixture
def claims_only(monkeypatch):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", goal_maker_mod.CLAIM_TEMPLATES)


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
    r"Convince \w+ \w+ that \w+ \w+ waters down the ale",
    r"Get half of the tavern regulars to believe \w+ \w+ waters down the ale",
    r"Half of the village believes the well is cursed",
)


@pytest.mark.usefixtures("claims_only")
def test_draw_fills_personal_circle_and_village_templates():
    town = town_of(AGNES, HOB, *FARMERS)
    drawn = [g for seed in range(200) for g in goal_maker(town, ALE, TAVERN, rng=random.Random(seed)).draw("p-1", set(), 1)]
    by_text = {g.text: g for g in drawn}
    assert all(any(re.fullmatch(p, t) for p in PATTERNS) for t in by_text)
    personal = by_text["Convince Agnes Thatch that Hob Miller waters down the ale"]
    assert personal.audience == (AGNES.id,) and personal.subject == HOB.id and personal.audience_label == "Agnes Thatch"
    assert personal.claim_text == "Hob Miller waters down the ale" and personal.evidence == ("Hob Miller waters down the ale",)
    assert personal.believer_question == "Does this villager believe Hob Miller waters down the ale?"
    circle = by_text["Get half of the tavern regulars to believe Hob Miller waters down the ale"]
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
    assert goal.text == "Convince Cob Smith that Hob Miller cheats at dice" and goal.audience == (COB.id,)
    (accusation_state, _), (mark_state, mark_question) = jev.requests
    assert accusation_state["subject"]["name"] == "Hob Miller"
    assert accusation_state["subject"]["held_against_them"] == [AGNES.grudge]
    assert mark_state["rumor"] == "Hob Miller cheats at dice" and HOB.id not in mark_question.criteria


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
    (goal,) = await maker.hand("p-1", {"hob miller waters down the ale"}, 1, subjects={AGNES.id, COB.id})
    (_, question), _ = jev.requests
    assert list(question.criteria.values()) == ["cheats at dice"]
    assert goal.claim_text == "Hob Miller cheats at dice"


@pytest.mark.usefixtures("claims_only")
def test_a_subject_with_every_accusation_taken_is_not_drawn():
    maker = goal_maker(town_of(AGNES, HOB), Pool((Entry("waters down the ale", 2),), ()))
    for _ in range(20):
        (goal,) = maker.draw("p-1", {"hob miller waters down the ale"}, 1)
        assert goal.subject == AGNES.id


@pytest.mark.usefixtures("claims_only")
def test_subjects_never_repeat_in_a_hand():
    town = town_of(AGNES, HOB, COB, DOT)
    for seed in range(30):
        hand = goal_maker(town, NO_CLAIMS, rng=random.Random(seed)).draw("p-1", set())
        subjects = [g.subject for g in hand]
        assert len(hand) == 3 and len(set(subjects)) == 3


def test_world_goal_from_pool_not_taken():
    pool = Pool((), (Entry("the well is cursed", 1), Entry("a dragon lives in the hills", 2)))
    maker = goal_maker(town_of(AGNES), pool)
    first = maker.world_goal({"the well is cursed"})
    assert first.id == "world-1" and first.claim_text == "a dragon lives in the hills"
    assert first.text == "Two thirds of the village believes a dragon lives in the hills"
    assert first.target_fraction == WORLD_FRACTION and first.audience == () and first.owner is None
    assert first.believer_question == "Does this villager believe a dragon lives in the hills?"
    assert maker.world_goal(set()).id == "world-2"


def test_world_goal_falls_back_when_every_claim_is_taken():
    pool = Pool((), (Entry("the well is cursed", 1),))
    goal = goal_maker(town_of(AGNES), pool).world_goal({"the well is cursed"})
    assert goal.claim_text == "the well is cursed"


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


@pytest.mark.usefixtures("claims_only")
@pytest.mark.asyncio
async def test_hand_loads_baselines_and_publishes_evidence():
    baselines = LoggingBaselines()
    maker = goal_maker(town_of(AGNES, HOB, *FARMERS), baselines=baselines)
    dealt = await maker.hand("p-1", set())
    assert baselines.loaded == [g.claim_text for g in dealt] and baselines.persisted == [False]
    assert maker.overhear.generated_ids() == {g.id for g in dealt}


@pytest.mark.usefixtures("claims_only")
@pytest.mark.asyncio
async def test_drawn_goals_are_published_when_prepared():
    baselines = LoggingBaselines()
    maker = goal_maker(town_of(AGNES, HOB, *FARMERS), baselines=baselines)
    drawn = maker.draw("p-1", set())
    world = maker.world_goal(set())
    assert maker.overhear.generated_ids() == set()
    await maker.prepare()
    assert baselines.loaded == [g.claim_text for g in (*drawn, world)]
    assert maker.overhear.generated_ids() == {g.id for g in drawn}
    assert f"goal:{world.id}" in maker.overhear._evidence
    await maker.prepare()
    assert len(baselines.loaded) == 4


@pytest.mark.asyncio
async def test_generated_ids_unique_across_players():
    maker = goal_maker(town_of(AGNES, HOB, *FARMERS), ids=Ids())
    first, second = await maker.hand("p-1", set()), await maker.hand("p-2", set())
    ids = [g.id for g in first + second]
    assert len(set(ids)) == 6 and all(i.startswith(("gen-p-1-", "gen-p-2-")) for i in ids)


@pytest.mark.usefixtures("claims_only")
def test_goals_name_villagers_in_full():
    town = town_of(AGNES, HOB, Villager("v9", "Agnes Reed", "baker", "kind", "", [], {}))
    drawn = [g for seed in range(50) for g in goal_maker(town, NO_CLAIMS, rng=random.Random(seed)).draw("p-1", set(), 1)]
    labels = {g.audience_label for g in drawn}
    assert labels <= {"Agnes Thatch", "Agnes Reed", "Hob Miller"} and "Agnes Thatch" in labels
    assert all("Agnes " in g.claim_text for g in drawn if g.subject in (AGNES.id, "v9"))


ACTION_CASES = (
    ("confront", r"Get Agnes Thatch to confront Hob Miller"),
    ("leave_town", r"Drive Hob Miller out of town"),
    ("found_group", r"Get someone to start a group against Hob Miller"),
    ("join_group", r"Get Agnes Thatch to join the Plough Guild"),
    ("exodus", r"Drive [23] villagers out of town"),
)


@pytest.mark.parametrize(("kind", "pattern"), ACTION_CASES)
def test_draw_fills_action_templates(monkeypatch, kind, pattern):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", (kind,))
    town = town_of(AGNES, HOB, COB)
    group = town.found_group(HOB.id, "The Plough Guild", "fair prices")
    town.join(COB.id, group.id)
    (goal,) = goal_maker(town).draw("p-1", set(), 1, subjects={AGNES.id, COB.id})
    assert re.fullmatch(pattern, goal.text)
    assert (goal.kind, goal.action, goal.hold_checks, goal.claim_text, goal.evidence) == ("action", kind, 1, "", ())
    assert goal.long_shot == (kind in ("leave_town", "exodus"))
    if kind in ("confront", "join_group"):
        assert (goal.actor, goal.audience, goal.audience_label) == (AGNES.id, (AGNES.id,), "Agnes Thatch")
    else:
        assert goal.actor is None and goal.audience == ()
    if kind == "exodus":
        assert goal.count in (2, 3) and goal.subject is None
    elif kind == "join_group":
        assert goal.target == group.id and goal.subject is None
    else:
        assert goal.subject == goal.target == HOB.id


def test_join_needs_a_group_of_two_and_an_outsider(monkeypatch):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", ("join_group",))
    town = town_of(AGNES, HOB)
    town.found_group(HOB.id, "The Plough Guild", "fair prices")
    assert goal_maker(town).draw("p-1", set(), 1) == []


@pytest.mark.asyncio
async def test_jev_picks_the_actor(monkeypatch):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", ("confront",))
    jev = ChoosingJev("Cob Smith")
    (goal,) = await goal_maker(town_of(AGNES, HOB, COB), jev=jev).hand("p-1", set(), 1, subjects={AGNES.id, COB.id})
    assert goal.text == "Get Cob Smith to confront Hob Miller" and goal.actor == COB.id
    (state, question), = jev.requests
    assert state["subject"]["name"] == "Hob Miller" and HOB.id not in question.criteria


@pytest.mark.asyncio
async def test_action_goals_skip_evidence_and_trace(monkeypatch):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", ("confront",))
    baselines = LoggingBaselines()
    maker = goal_maker(town_of(AGNES, HOB), baselines=baselines)
    maker.draw("p-1", set(), 1)
    await maker.prepare()
    dealt = await maker.hand("p-1", set(), 1)
    assert baselines.loaded == [] and maker.overhear.generated_ids() == set() and dealt[0].kind == "action"


def test_claims_and_actions_come_up_about_equally():
    town = town_of(AGNES, HOB, COB, DOT, *FARMERS)
    maker = goal_maker(town, ALE, TAVERN, rng=random.Random(5))
    kinds = Counter(maker.draw("p-1", set(), 1)[0].kind for _ in range(2000))
    assert 0.45 < kinds["action"] / 2000 < 0.55


def test_no_action_is_drawn_twice_in_a_hand(monkeypatch):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", ("exodus", "join_group"))
    town = town_of(AGNES, HOB, COB, DOT)
    group = town.found_group(HOB.id, "The Plough Guild", "fair prices")
    town.join(COB.id, group.id)
    for seed in range(30):
        maker = goal_maker(town, rng=random.Random(seed))
        hand = maker.draw("p-1", set())
        assert sorted(g.action for g in hand) == ["exodus", "join_group"]
        assert maker.draw("p-1", set(), 1, held=hand) == []


def test_exodus_n_is_drawn_only_when_exodus_is_picked(monkeypatch):
    monkeypatch.setattr(goal_maker_mod, "TEMPLATES", ("village", "exodus"))
    for seed in range(20):
        rng = random.Random(seed)
        sizes = []
        choice = rng.choice
        monkeypatch.setattr(rng, "choice", lambda seq: sizes.append(set(seq) <= {2, 3}) or choice(seq))
        (goal,) = goal_maker(town_of(AGNES, HOB), ALE, rng=rng).draw("p-1", set(), 1)
        assert sizes.count(True) == (goal.action == "exodus")
