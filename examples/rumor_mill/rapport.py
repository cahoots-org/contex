"""How taken each villager is with each player's rumors, and with the player."""

from examples.rumor_mill.config import Config


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def interest_words(x: float) -> str:
    """How taken a villager is with a rumor, in words."""
    if x >= 0.5:
        return "eager to hear more"
    if x >= 0.15:
        return "curious"
    if x > -0.15:
        return "lukewarm"
    if x > -0.45:
        return "losing interest"
    return "about out of patience"


def rapport_words(x: float) -> str:
    """How a villager feels about a player, in words."""
    if x >= 0.4:
        return "fond of the newcomer"
    if x >= 0.1:
        return "warm toward the newcomer"
    if x > -0.1:
        return "neutral toward the newcomer"
    if x > -0.4:
        return "cool toward the newcomer"
    return "tired of the newcomer"


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

    def started(self, vid: str, player: str, rumor: str) -> bool:
        """Whether she has taken any interest in the rumor yet."""
        return (vid, player, rumor) in self._interest

    def mood(self, vid: str, player: str, rumor: str, greet: bool = False) -> str:
        """How she feels about the rumor in words; greet adds how she feels about the player, to greet them by."""
        mood = interest_words(self.interest(vid, player, rumor))
        if greet:
            mood += f", and {rapport_words(self.rapport(vid, player))}. Greet the newcomer the way you feel about them"
        return mood
