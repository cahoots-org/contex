"""Whether the game is still on, and how it ended."""

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class Summary:
    outcome: str
    # Goal text and whole percent.
    goals: list[tuple[str, int]]
    reasons: list[str]


class Game:
    def __init__(self, feed):
        self.feed = feed
        self.state = "playing"
        self.summary: Summary | None = None
        self.finished = asyncio.Event()

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
