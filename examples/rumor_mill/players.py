"""Player identity and the players of one world."""

import secrets
from collections.abc import Iterator

from examples.rumor_mill.goals import Trophy
from examples.rumor_mill.player import Player

# The one person playing in the terminal.
LOCAL_OWNER = "local"
# Real seconds a run-out player waits before a reload starts them over.
RUN_OUT_LOCKOUT_SECONDS = 600


def new_player_id() -> str:
    return "p-" + secrets.token_hex(8)


def new_owner_id() -> str:
    return "o-" + secrets.token_hex(8)


class Players:
    """The players in one world, by id, and each owner's trophy case."""

    def __init__(self, feed) -> None:
        self.feed = feed
        self._by_id: dict[str, Player] = {}
        self._latest: dict[str, Player] = {}
        self.cases: dict[str, list[Trophy]] = {}
        # Trophy lines for an owner with no live run, sent when their next run starts.
        self.pending: dict[str, list[tuple[Trophy, str]]] = {}

    def add(self, player: Player) -> None:
        self._by_id[player.id] = player
        self._latest[player.owner] = player

    def get(self, player_id: str) -> Player | None:
        return self._by_id.get(player_id)

    def live(self, owner: str) -> Player | None:
        """The owner's current run, over or not."""
        return self._latest.get(owner)

    def __iter__(self) -> Iterator[Player]:
        return iter(self._by_id.values())

    def any_playing(self) -> bool:
        """Whether some player's game is not over."""
        return any(not p.over() for p in self._by_id.values())

    def at(self, place: str) -> list[Player]:
        """The players at place whose game is not over."""
        return [p for p in self._by_id.values() if p.place == place and not p.over()]

    def award(self, owner: str, trophy: Trophy, line: str) -> None:
        """Put the trophy in the owner's case and tell their live run, or keep the line for their next one."""
        self.cases.setdefault(owner, []).append(trophy)
        live = next((p for p in self._by_id.values() if p.owner == owner and not p.over()), None)
        if live is None:
            self.pending.setdefault(owner, []).append((trophy, line))
        else:
            self.feed.on_trophy(trophy, line, player=live.id)

    def deliver_pending(self, player: Player) -> None:
        """Send the player the trophy lines their owner won while they had no live run."""
        for trophy, line in self.pending.pop(player.owner, []):
            self.feed.on_trophy(trophy, line, player=player.id)
