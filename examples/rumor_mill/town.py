"""Town state: villagers, their places, and groups."""

from examples.rumor_mill.models import Group, Villager

PLACES = ("square", "tavern", "market", "well", "mill", "chapel", "gate")
START_PLACE = "square"
GATE = "gate"
PLAYER_ID = "newcomer"


class Town:
    def __init__(self) -> None:
        self._villagers: dict[str, Villager] = {}
        self._places: dict[str, str] = {}
        self._groups: dict[str, Group] = {}
        self.departed: list[Villager] = []

    @property
    def alive_count(self) -> int:
        return len(self._villagers)

    def add(self, v: Villager, place: str = START_PLACE) -> None:
        self._villagers[v.id] = v
        self._places[v.id] = place

    def remove(self, vid: str) -> Villager:
        v = self._villagers.pop(vid)
        self._places.pop(vid, None)
        for g in self._groups.values():
            g.members.discard(vid)
        self.departed.append(v)
        return v

    def get(self, vid: str) -> Villager:
        return self._villagers[vid]

    def place_of(self, vid: str) -> str:
        return self._places[vid]

    def move(self, vid: str, place: str) -> None:
        if place not in PLACES:
            raise ValueError(f"unknown place: {place}")
        self._places[vid] = place

    def present(self, place: str, exclude: str | None = None) -> list[Villager]:
        return [
            self._villagers[vid]
            for vid, p in self._places.items()
            if p == place and vid != exclude
        ]

    def find(self, name: str) -> list[Villager]:
        needle = name.lower()
        exact = [v for v in self._villagers.values() if v.name.lower() == needle]
        if exact:
            return exact
        return [v for v in self._villagers.values() if needle in v.name.lower()]

    def found_group(self, founder_id: str, name: str, creed: str) -> Group:
        g = Group(f"g-{len(self._groups) + 1}", name, creed, founder_id, {founder_id})
        self._groups[g.id] = g
        return g

    def join(self, vid: str, gid: str) -> None:
        self._groups[gid].members.add(vid)

    def leave(self, vid: str, gid: str) -> None:
        self._groups[gid].members.discard(vid)

    def member_groups(self, vid: str) -> list[Group]:
        return [g for g in self._groups.values() if vid in g.members]

    def visible_groups(self, vid: str) -> list[Group]:
        place = self._places[vid]
        return [
            g
            for g in self._groups.values()
            if vid not in g.members
            and any(self._places.get(m) == place for m in g.members)
        ]
