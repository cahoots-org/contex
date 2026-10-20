from examples.rumor_mill.feed import Feed
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.town import Town


def make():
    town, lines, lineage = Town(), [], Lineage()
    for vid, name in (("v1", "Bert"), ("v2", "Marta")):
        town.add(Villager(vid, name, "baker", "calm", "none", [], {}))
    lineage.record("w-1", None, player=True)
    lineage.record("u-1", "w-1")
    lineage.record("u-2", None)
    return Feed(lineage, town, out=lines.append), lines


def utt(uid):
    return Utterance(uid, "v1", "Bert", "tavern", "the miller waters his flour", "w-1")


def test_only_player_rooted_deliveries_print():
    feed, lines = make()
    feed.on_delivery(utt("u-2"), ["v2"])
    assert lines == []
    feed.on_delivery(utt("u-1"), ["v2"])
    assert len(lines) == 1
    whisper = Utterance("w-1", "newcomer", "The newcomer", "tavern", "psst", None, "whispered to you")
    feed.on_delivery(whisper, ["v2"])
    assert len(lines) == 1


def test_single_and_group_line_formats():
    feed, lines = make()
    feed.on_delivery(utt("u-1"), ["v2"])
    feed.on_delivery(utt("u-1"), ["v2", "v1", "v3", "v4"])
    assert lines == [
        'Bert told Marta: "the miller waters his flour"',
        'Bert said at the tavern (heard by 4): "the miller waters his flour"',
    ]

