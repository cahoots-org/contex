"""Player identity and the players of one world."""

import secrets
from collections.abc import Iterator

from examples.rumor_mill.player import Player


def new_player_id() -> str:
    return "p-" + secrets.token_hex(8)


class Players:
    """The players in one world, by id."""

    def __init__(self) -> None:
        self._by_id: dict[str, Player] = {}

    def add(self, player: Player) -> None:
        self._by_id[player.id] = player

    def get(self, player_id: str) -> Player | None:
        return self._by_id.get(player_id)

    def __iter__(self) -> Iterator[Player]:
        return iter(self._by_id.values())

    def at(self, place: str) -> list[Player]:
        """The players at place whose game is not over."""
        return [p for p in self._by_id.values() if p.place == place and not p.over()]
