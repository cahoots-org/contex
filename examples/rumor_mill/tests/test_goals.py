from examples.rumor_mill.goals import WORLD_FRACTION, Goal, Trophy, claim, trophy_line, world_goal


def test_world_goal_raises_the_bar_on_a_claim():
    g = world_goal(Goal("world-1", "Half of the village believes the cat is a spy", "belief", claim_text="the cat is a spy"))
    assert (g.id, g.target_fraction) == ("world-1", WORLD_FRACTION)
    assert g.text == "Two thirds of the village believes the cat is a spy"


def test_claim_prefers_claim_text():
    assert claim(Goal("x", "Convince Agnes that Hob steals geese", "belief", claim_text="Hob steals geese")) == "Hob steals geese"


def test_trophy_line_never_knew():
    line = trophy_line(Trophy("personal", ("x",), 1), "Hob steals geese", unseen="Hob")
    assert line.endswith(" And Hob never knew.")
    assert "never knew" not in trophy_line(Trophy("personal", ("x",), 1), "Hob steals geese")


def test_trophy_json_carries_long_shot():
    assert Trophy("personal", ("x",), 1).to_json()["long_shot"] is False
    assert Trophy("world", ("x",), 1, long_shot=True).to_json()["long_shot"] is True
