"""The village clock and memory, and how a player's game ended."""

import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime


# The parts of a game day, in order; the clock moves through them evenly.
PHASES = ("morning", "afternoon", "evening", "night")
MEMORY_PLAYERS = 20


@dataclass(frozen=True)
class Summary:
    outcome: str
    # Goal text and whole percent.
    goals: list[tuple[str, int]]
    reasons: list[str]


class Game:
    def __init__(self, memory_moves: int = 20, day_seconds: float = 120.0):
        self.memory_moves = memory_moves
        self.day_seconds = day_seconds
        # ponytail: remembers at most MEMORY_PLAYERS active players' worth of moves; raise it if more play at once.
        self._moves: deque[tuple[float, str]] = deque(maxlen=memory_moves * MEMORY_PLAYERS)
        self.day, self.phase = 1, PHASES[0]
        self.phase_started = time.time()

    def to_state(self) -> dict:
        """The day, phase, how far through the phase it is (0 to 1), and recent moves."""
        part = self.day_seconds / len(PHASES)
        return {"day": self.day, "phase": self.phase,
                "elapsed": min(1.0, (time.time() - self.phase_started) / part),
                "moves": [list(m) for m in self._moves]}

    def restore(self, state: dict) -> None:
        """Replace the clock and moves with a saved state; the phase resumes where it left off."""
        self.day, self.phase = state["day"], state["phase"]
        self.phase_started = time.time() - state["elapsed"] * self.day_seconds / len(PHASES)
        self._moves.clear()
        self._moves.extend((at, player) for at, player in state["moves"])

    def tick(self) -> None:
        """Move the village clock on to the next part of the day."""
        at = PHASES.index(self.phase) + 1
        self.day += at // len(PHASES)
        self.phase = PHASES[at % len(PHASES)]
        self.phase_started = time.time()

    def clock(self) -> dict:
        """The day, its part, how far through the day it is (0 to 1), and how long a day lasts in seconds."""
        part = self.day_seconds / len(PHASES)
        into = min(1.0, (time.time() - self.phase_started) / part)
        return {"day": self.day, "phase": self.phase, "fraction": (PHASES.index(self.phase) + into) / len(PHASES),
                "day_seconds": self.day_seconds}

    def moved(self, player: str) -> None:
        """Count one of a player's moves; the village's memory is measured in these, not in time."""
        self._moves.append((time.time(), player))

    def memory_since(self) -> str | None:
        """When the oldest move still remembered was made, as ISO-8601; None until there have been that many.

        The village remembers memory_moves moves for each player who moved within the last game day.
        """
        cutoff = time.time() - self.day_seconds
        active = len({player for at, player in self._moves if at >= cutoff})
        needed = min(self.memory_moves * max(1, active), self._moves.maxlen)
        if len(self._moves) < needed:
            return None
        return datetime.fromtimestamp(self._moves[-needed][0], UTC).isoformat()
