import pytest

from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.town import Town


def v(vid, name):
    return Villager(vid, name, "baker", "kind", "none", [], {})


def test_present_excludes_given_id():
    t = Town()
    t.add(v("a", "Ann"))
    t.add(v("b", "Bob"))
    assert [x.id for x in t.present("square", exclude="a")] == ["b"]


def test_find_exact_beats_substring():
    t = Town()
    t.add(v("a", "Bert"))
    t.add(v("b", "Bertha"))
    assert [x.id for x in t.find("bert")] == ["a"]


def test_remove_records_departure_and_drops_membership():
    t = Town()
    t.add(v("a", "Ann"))
    g = t.found_group("a", "Club", "creed")
    gone = t.remove("a")
    assert gone.id == "a" and t.departed == [gone]
    assert "a" not in g.members and t.alive_count == 0


def test_visible_groups_needs_member_in_same_place():
    t = Town()
    t.add(v("a", "Ann"))
    t.add(v("b", "Bob"))
    g = t.found_group("a", "Club", "creed")
    assert t.visible_groups("b") == [g]
    t.move("a", "tavern")
    assert t.visible_groups("b") == []
    t.move("b", "tavern")
    assert t.visible_groups("b") == [g]
    t.join("b", g.id)
    assert t.visible_groups("b") == []


def test_found_group_makes_founder_member():
    t = Town()
    t.add(v("a", "Ann"))
    g = t.found_group("a", "Club", "creed")
    assert g.id == "g-1" and g.members == {"a"}
    assert t.member_groups("a") == [g]
    t.leave("a", g.id)
    assert t.member_groups("a") == []


def test_move_rejects_unknown_place():
    t = Town()
    t.add(v("a", "Ann"))
    with pytest.raises(ValueError):
        t.move("a", "moon")


def test_utterance_to_data_is_one_sentence():
    u = Utterance("u-1", "a", "Bert", "tavern", "the oven is cold", "w-1", verb="said")
    assert u.to_data() == {"id": "u-1", "text": "Bert said: the oven is cold"}


def test_villagers_lists_current_roster():
    town = Town()
    a, b = v("v1", "Ann"), v("v2", "Bob")
    town.add(a)
    town.add(b)
    town.remove("v1")
    assert town.villagers == [b]
