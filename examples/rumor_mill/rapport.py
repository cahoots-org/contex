"""How taken each villager is with each player's rumors, and with the player."""

from examples.rumor_mill.config import Config


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


class Rapport:
    """Each villager's interest in each player's rumors, and her rapport with each player, all in [-1, 1]."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._rapport: dict[tuple[str, str], float] = {}
        self._interest: dict[tuple[str, str, str], float] = {}

    def rapport(self, vid: str, player: str) -> float:
        """How much the villager likes talking with player."""
        return self._rapport.get((vid, player), 0.0)

    def interest(self, vid: str, player: str, rumor: str) -> float:
        """How taken the villager is with player's rumor; half her rapport with them until it has moved."""
        return self._interest.get((vid, player, rumor), 0.5 * self.rapport(vid, player))

    def move(self, vid: str, player: str, rumor: str, delta: float) -> float:
        """Move her interest in the rumor by delta, and her rapport by rapport_share of it; returns the new interest."""
        new = self._interest[(vid, player, rumor)] = _clamp(self.interest(vid, player, rumor) + delta)
        self.sour(vid, player, self.config.rapport_share * delta)
        return new

    def sour(self, vid: str, player: str, amount: float) -> None:
        """Move her rapport with player by amount."""
        self._rapport[(vid, player)] = _clamp(self.rapport(vid, player) + amount)

    def forget(self, vid: str, player: str, rumor: str) -> None:
        """Drop her interest in the rumor, so it starts again from her rapport."""
        self._interest.pop((vid, player, rumor), None)
