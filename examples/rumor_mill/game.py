"""Whether the game is still on, and how it ended."""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Summary:
    outcome: str
    # Goal text and whole percent.
    goals: list[tuple[str, int]]
    reasons: list[str]


class Game:
    def __init__(self, feed, memory_moves: int = 20):
        self.feed = feed
        self._moves: deque[float] = deque(maxlen=memory_moves)
        self.state = "playing"
        self.summary: Summary | None = None
        self.finished = asyncio.Event()

    def moved(self) -> None:
        """Count one of the newcomer's moves; the village's memory is measured in these, not in time."""
        self._moves.append(time.time())

    def memory_since(self) -> str | None:
        """When the oldest move still remembered was made, as ISO-8601; None until there have been that many."""
        if len(self._moves) < (self._moves.maxlen or 0):
            return None
        return datetime.fromtimestamp(self._moves[0], UTC).isoformat()

    def win(self, summary: Summary) -> None:
        self._finish("won", summary)

    def lose(self, summary: Summary) -> None:
        self._finish("lost", summary)

    def _finish(self, state: str, summary: Summary) -> None:
        """End the game once; announce it, then silence the feed."""
        if self.finished.is_set():
            return
        self.state, self.summary = state, summary
        self.feed.on_game_over(summary)
        self.feed.closed = True
        self.finished.set()
