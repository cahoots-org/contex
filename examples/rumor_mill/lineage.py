"""Id generation and rumor ancestry tracking."""

from collections import defaultdict


class Ids:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def next(self, prefix: str) -> str:
        self._counts[prefix] += 1
        return f"{prefix}-{self._counts[prefix]}"


class Lineage:
    def __init__(self) -> None:
        self._parent: dict[str, str | None] = {}
        self._player: set[str] = set()

    def record(self, uid: str, parent: str | None, player: bool = False) -> None:
        self._parent[uid] = parent
        if player:
            self._player.add(uid)

    def root(self, uid: str) -> str:
        while self._parent.get(uid) is not None:
            uid = self._parent[uid]
        return uid

    def is_player_rooted(self, uid: str) -> bool:
        return self.root(uid) in self._player
