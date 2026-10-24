"""Id generation and rumor ancestry tracking."""

from collections import defaultdict


class Ids:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def next(self, prefix: str) -> str:
        self._counts[prefix] += 1
        return f"{prefix}-{self._counts[prefix]}"

    def ensure_past(self, prefix: str, n: int) -> None:
        """Make the next id for prefix come after n."""
        self._counts[prefix] = max(self._counts[prefix], n)

    def to_state(self) -> dict:
        """The counter for each prefix."""
        return dict(self._counts)

    def restore(self, state: dict) -> None:
        """Replace the counters with a saved state."""
        self._counts = defaultdict(int, state)


class Lineage:
    def __init__(self) -> None:
        self._parent: dict[str, str | None] = {}
        self._player: dict[str, str] = {}

    def record(self, uid: str, parent: str | None, player: str | None = None) -> None:
        """Record uid's parent; player is the id of the player whose whisper uid is."""
        self._parent[uid] = parent
        if player is not None:
            self._player[uid] = player

    def to_state(self) -> dict:
        """Parents and players as plain JSON."""
        return {"parent": dict(self._parent), "player": dict(self._player)}

    def restore(self, state: dict) -> None:
        """Replace parents and players with a saved state."""
        self._parent = dict(state["parent"])
        self._player = dict(state["player"])

    def root(self, uid: str) -> str:
        while self._parent.get(uid) is not None:
            uid = self._parent[uid]
        return uid

    def depth(self, uid: str) -> int:
        """Hops between uid and its root."""
        n = 0
        while self._parent.get(uid) is not None:
            uid, n = self._parent[uid], n + 1
        return n

    def player_of(self, uid: str | None) -> str | None:
        """The player whose whisper uid's lineage roots in."""
        return None if uid is None else self._player.get(self.root(uid))

    def is_player_rooted(self, uid: str) -> bool:
        return self.player_of(uid) is not None

    def is_whisper(self, uid: str) -> bool:
        """Whether uid is a player's own whisper."""
        return self.is_player_rooted(uid) and self.root(uid) == uid
