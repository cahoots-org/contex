import logging
from types import SimpleNamespace

import pytest

from examples.rumor_mill.models import Villager
from examples.rumor_mill.routine import LEISURE_INSTRUCTIONS, WORK_INSTRUCTIONS, Routine
from examples.rumor_mill.town import GATE, PLACES


def villager(vid="v1", name="Ada"):
    return Villager(vid, name, "miller", "shy", "", [], {})


class CountingJev:
    def __init__(self, work="mill", leisure="tavern", fail=()):
        self.answers = {"work": work, "leisure": leisure}
        self.fail = set(fail)
        self.calls = []

    async def system_one(self, state, questions):
        self.calls.append((state, questions))
        if state["name"] in self.fail:
            raise RuntimeError("jev down")
        return SimpleNamespace(choices={k: SimpleNamespace(choice=self.answers[k]) for k in questions})


@pytest.mark.asyncio
async def test_load_asks_once_per_villager_and_writes_the_sidecar(tmp_path):
    path = tmp_path / "places.json"
    jev = CountingJev()
    routine = Routine(jev, str(path))
    await routine.load([villager("v1", "Ada"), villager("v2", "Bo")])
    assert len(jev.calls) == 2
    state, questions = jev.calls[0]
    assert state == {"name": "Ada", "occupation": "miller", "personality": "shy"}
    assert set(questions) == {"work", "leisure"}
    assert questions["work"].instructions == WORK_INSTRUCTIONS
    assert questions["leisure"].instructions == LEISURE_INSTRUCTIONS
    assert set(questions["work"].criteria) == set(PLACES) - {GATE}
    assert routine.place("v2", "morning") == "mill"
    assert '"v2"' in path.read_text()


@pytest.mark.asyncio
async def test_load_reuses_the_sidecar(tmp_path):
    path = str(tmp_path / "places.json")
    await Routine(CountingJev(), path).load([villager()])
    jev = CountingJev(work="well")
    routine = Routine(jev, path)
    await routine.load([villager()])
    assert jev.calls == []
    assert routine.place("v1", "morning") == "mill"


@pytest.mark.asyncio
async def test_a_renamed_or_invalid_entry_is_asked_again(tmp_path):
    path = tmp_path / "places.json"
    path.write_text('{"v1": {"name": "Old", "work": "mill", "leisure": "tavern"},'
                    ' "v2": {"name": "Bo", "work": "nowhere", "leisure": "tavern"}}')
    jev = CountingJev(work="well")
    routine = Routine(jev, str(path))
    await routine.load([villager("v1", "Ada"), villager("v2", "Bo")])
    assert len(jev.calls) == 2
    assert routine.place("v1", "morning") == "well"
    assert routine.place("v2", "morning") == "well"


@pytest.mark.asyncio
async def test_an_unreadable_sidecar_is_rewritten(tmp_path):
    path = tmp_path / "places.json"
    path.write_text("{not json")
    routine = Routine(CountingJev(), str(path))
    await routine.load([villager()])
    again = CountingJev()
    await Routine(again, str(path)).load([villager()])
    assert again.calls == []


@pytest.mark.asyncio
async def test_place_follows_the_part_of_the_day(tmp_path):
    routine = Routine(CountingJev(work="mill", leisure="tavern"), str(tmp_path / "p.json"))
    await routine.load([villager()])
    assert [routine.place("v1", p) for p in ("morning", "afternoon", "evening", "night")] == [
        "mill", "square", "tavern", "mill",
    ]
    assert routine.place("nobody", "morning") is None


@pytest.mark.asyncio
async def test_a_failed_choice_leaves_the_villager_without_a_routine(tmp_path, caplog):
    path = tmp_path / "places.json"
    jev = CountingJev(fail={"Bo"})
    routine = Routine(jev, str(path))
    with caplog.at_level(logging.WARNING):
        await routine.load([villager("v1", "Ada"), villager("v2", "Bo")])
    assert routine.place("v1", "morning") == "mill"
    assert routine.place("v2", "morning") is None
    assert "v2" in caplog.text
    assert "Bo" not in caplog.text
    assert '"v2"' not in path.read_text()
    again = CountingJev()
    await Routine(again, str(path)).load([villager("v1", "Ada"), villager("v2", "Bo")])
    assert [c[0]["name"] for c in again.calls] == ["Bo"]


@pytest.mark.asyncio
async def test_add_writes_the_newcomer(tmp_path):
    path = str(tmp_path / "places.json")
    routine = Routine(CountingJev(), path)
    await routine.add(villager("n1", "Newt"))
    assert routine.place("n1", "evening") == "tavern"
    again = CountingJev()
    await Routine(again, path).load([villager("n1", "Newt")])
    assert again.calls == []
