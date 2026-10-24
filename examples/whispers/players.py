"""Player identity and the players of one world."""

import secrets
import time
from collections.abc import Callable, Iterator

from examples.whispers.goals import Trophy, trophy_from_state
from examples.whispers.player import Player
from examples.whispers.usage import Usage

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

    def __init__(self, feed, usage: Usage | None = None) -> None:
        self.feed = feed
        self.usage = usage
        self._by_id: dict[str, Player] = {}
        self._latest: dict[str, Player] = {}
        self.cases: dict[str, list[Trophy]] = {}
        # Trophy lines for an owner with no live run, sent when their next run starts.
        self.pending: dict[str, list[tuple[Trophy, str]]] = {}

    def to_state(self) -> dict:
        """Every run, each owner's trophy case and pending trophy lines as plain JSON."""
        return {"runs": [p.to_state() for p in self._by_id.values()],
                "cases": {owner: [t.to_json() for t in case] for owner, case in self.cases.items()},
                "pending": {owner: [[t.to_json(), line] for t, line in lines] for owner, lines in self.pending.items()}}

    def restore(self, state: dict, make: Callable[[str, str], Player]) -> None:
        """Replace the runs, cases and pending lines with a saved state; make(id, owner) builds a wired run."""
        self._by_id, self._latest = {}, {}
        for run in state["runs"]:
            player = make(run["id"], run["owner"])
            player.restore(run)
            self.add(player)
        self.cases = {owner: [trophy_from_state(t) for t in case] for owner, case in state["cases"].items()}
        self.pending = {owner: [(trophy_from_state(t), line) for t, line in lines]
                        for owner, lines in state["pending"].items()}

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
        """Whether some active player's game is not over, and the village is not resting for the day."""
        if self.resting():
            return False
        return any(p.active() and not p.over() for p in self._by_id.values())

    def resting(self) -> bool:
        """Whether the village is resting for the day."""
        return self.usage is not None and self.usage.resting()

    def at(self, place: str) -> list[Player]:
        """The active players at place whose game is not over."""
        return [p for p in self._by_id.values() if p.place == place and p.active() and not p.over()]

    def prune(self) -> list[Player]:
        """Drop and return the runs that never played and whose owner has no trophies: those older than
        idle_after_seconds, and the oldest past the newest run's max_idle_runs."""
        now = time.time()
        idle = sorted(
            (p for p in self._by_id.values()
             if not p.ever_active() and p.owner not in self.pending and p.owner not in self.cases),
            key=lambda p: p.created_at,
        )
        excess = len(idle) - idle[-1].config.max_idle_runs if idle else 0
        stale = [p for i, p in enumerate(idle) if i < excess or now - p.created_at >= p.config.idle_after_seconds]
        for p in stale:
            del self._by_id[p.id]
            if self._latest.get(p.owner) is p:
                del self._latest[p.owner]
        return stale

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
