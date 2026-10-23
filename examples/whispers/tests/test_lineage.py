from examples.whispers.lineage import Ids, Lineage
from examples.whispers.tests.helpers import PID


def test_root_follows_parents():
    ln = Lineage()
    ln.record("w-1", None, player=PID)
    ln.record("u-1", "w-1")
    ln.record("u-2", "u-1")
    assert ln.root("u-2") == "w-1"


def test_player_rooted_only_when_root_is_player():
    ln = Lineage()
    ln.record("w-1", None, player=PID)
    ln.record("u-1", "w-1")
    ln.record("u-9", None)
    ln.record("u-10", "u-9")
    assert ln.is_player_rooted("u-1")
    assert not ln.is_player_rooted("u-10")


def test_player_of_follows_the_root_to_its_player():
    ln = Lineage()
    ln.record("w-1", None, player="p-a")
    ln.record("u-1", "w-1")
    ln.record("w-2", None, player="p-b")
    ln.record("u-9", None)
    assert (ln.player_of("u-1"), ln.player_of("w-2")) == ("p-a", "p-b")
    assert ln.player_of("u-9") is None and ln.player_of(None) is None
    assert ln.is_whisper("w-2") and not ln.is_whisper("u-1")


def test_ids_are_monotonic_per_prefix():
    ids = Ids()
    assert [ids.next("u"), ids.next("u"), ids.next("w")] == ["u-1", "u-2", "w-1"]
