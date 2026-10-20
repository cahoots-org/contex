from examples.rumor_mill.contex_client import Match, new_matches


def test_new_matches_dedupes_across_needs_and_skips_seen():
    a, b, c = Match("a", {"t": 1}), Match("b", {"t": 2}), Match("c", {"t": 3})
    bundle = {"n1": [a, b], "n2": [b, c]}
    assert new_matches({"a"}, bundle) == [b, c]
    assert new_matches(set(), {}) == []
