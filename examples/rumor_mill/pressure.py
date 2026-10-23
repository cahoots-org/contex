"""How close each villager is to leaving town, and which player pushed them."""

from examples.rumor_mill.config import Config


class Pressure:
    """Each villager's pressure to leave town, from 0 up, with each player's share of it."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._level: dict[str, float] = {}
        self._shares: dict[str, dict[str, float]] = {}

    def add(self, vid: str, amount: float, player: str | None) -> float:
        """Raise the villager's pressure by amount, crediting player with it; returns the new pressure."""
        self._level[vid] = self.level(vid) + amount
        if player is not None:
            shares = self._shares.setdefault(vid, {})
            shares[player] = shares.get(player, 0.0) + amount
        return self._level[vid]

    def level(self, vid: str) -> float:
        return self._level.get(vid, 0.0)

    def words(self, vid: str) -> str:
        """How close the villager is to breaking, in words."""
        level = self.level(vid)
        if level >= self.config.pressure_breaking:
            return "at the end of their rope"
        if level >= self.config.pressure_shaken:
            return "shaken"
        return "steady"

    def decay(self) -> None:
        """Ease every villager's pressure by pressure_decay, never below 0, and each player's share in proportion."""
        for vid, level in self._level.items():
            eased = max(0.0, level - self.config.pressure_decay)
            self._level[vid] = eased
            shares = self._shares.get(vid, {})
            for player in shares:
                shares[player] *= eased / level if level else 0.0

    def culprit(self, vid: str) -> str | None:
        """The player with the largest share of the villager's pressure, or None when no player has any."""
        shares = {p: s for p, s in self._shares.get(vid, {}).items() if s > 0}
        return max(shares, key=shares.get) if shares else None

    def drop(self, vid: str) -> None:
        """Forget a villager who has left."""
        self._level.pop(vid, None)
        self._shares.pop(vid, None)
