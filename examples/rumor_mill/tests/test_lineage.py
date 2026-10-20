from examples.rumor_mill.lineage import Ids, Lineage


def test_root_follows_parents():
    ln = Lineage()
    ln.record("w-1", None, player=True)
    ln.record("u-1", "w-1")
    ln.record("u-2", "u-1")
    assert ln.root("u-2") == "w-1"


def test_player_rooted_only_when_root_is_player():
    ln = Lineage()
    ln.record("w-1", None, player=True)
    ln.record("u-1", "w-1")
    ln.record("u-9", None)
    ln.record("u-10", "u-9")
    assert ln.is_player_rooted("u-1")
    assert not ln.is_player_rooted("u-10")


def test_ids_are_monotonic_per_prefix():
    ids = Ids()
    assert [ids.next("u"), ids.next("u"), ids.next("w")] == ["u-1", "u-2", "w-1"]
