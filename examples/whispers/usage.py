"""Token counts per provider and what they cost at the configured prices."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime

from examples.whispers.config import Config

PROVIDERS = ("haiku", "jev")

# The player whose whisper set off the model calls made here; None is village overhead.
charged_to: ContextVar[str | None] = ContextVar("charged_to", default=None)


@contextmanager
def charging(player: str | None) -> Iterator[None]:
    """Charge the model calls made inside the block to player."""
    token = charged_to.set(player)
    try:
        yield
    finally:
        charged_to.reset(token)


def _utc_day() -> date:
    return datetime.fromtimestamp(time.time(), UTC).date()


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def _money(cost: float, lead: str = "") -> str:
    """Cost in dollars, with lead before a rounded amount, or "less than $0.01" when under a cent."""
    return "less than $0.01" if 0 < cost < 0.005 else f"{lead}${cost:.2f}"


class Usage:
    def __init__(self, config: Config):
        self.prices = {
            "haiku": (config.haiku_input_per_mtok, config.haiku_output_per_mtok),
            "jev": (config.jev_input_per_mtok, config.jev_output_per_mtok),
        }
        self.tokens = {p: [0, 0] for p in PROVIDERS}
        self.players: dict[str, float] = {}
        self.cap = config.daily_spend_cap
        self._day: date | None = None
        self._today = 0.0

    def to_state(self) -> dict:
        """Tokens, spend per player, and today's spend with its day."""
        return {"tokens": {p: list(c) for p, c in self.tokens.items()}, "players": dict(self.players),
                "today": self._today, "day": self._day.isoformat() if self._day else None}

    def restore(self, state: dict) -> None:
        """Replace the totals with a saved state."""
        self.tokens = {p: list(c) for p, c in state["tokens"].items()}
        self.players = dict(state["players"])
        self._today = state["today"]
        self._day = date.fromisoformat(state["day"]) if state["day"] else None

    def record(
        self, provider: str, input_tokens: int | None, output_tokens: int | None, player: str | None = None,
    ) -> None:
        """Add a call's tokens to the totals, and its cost to player's share when there is one."""
        counts = self.tokens[provider]
        counts[0] += input_tokens or 0
        counts[1] += output_tokens or 0
        dollars = self._dollars(provider, input_tokens or 0, output_tokens or 0)
        self._today = self.cost_today() + dollars
        self._day = _utc_day()
        if player is not None:
            self.players[player] = self.players.get(player, 0.0) + dollars

    def cost_today(self) -> float:
        """Estimated dollars spent since the last UTC midnight."""
        return self._today if self._day == _utc_day() else 0.0

    def resting(self) -> bool:
        """Whether today's spend has reached the daily cap."""
        return self.cap > 0 and self.cost_today() >= self.cap

    def _dollars(self, provider: str, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.prices[provider][0] + output_tokens * self.prices[provider][1]) / 1_000_000

    def cost(self, player: str | None = None) -> float:
        """Estimated dollars spent so far, in all or on player's behalf."""
        if player is not None:
            return self.players.get(player, 0.0)
        return sum(self._dollars(p, inp, out) for p, (inp, out) in self.tokens.items())

    def summary(self, player: str | None = None) -> str:
        """The cost so far as one line: the village total with its tokens, or player's share."""
        if player is not None:
            return f"Your rumors have cost {_money(self.cost(player), 'about ')} so far"
        parts = []
        for name, (inp, out) in (("Haiku", self.tokens["haiku"]), ("Jev", self.tokens["jev"])):
            if inp or out:
                parts.append(f"{name} {_tokens(inp)} in" + (f" / {_tokens(out)} out" if out else ""))
        detail = f" ({', '.join(parts)})" if parts else ""
        return f"Estimated cost so far: {_money(self.cost())}{detail}"

    def to_json(self, player: str | None = None) -> dict:
        """The summary and cost, in all or for player."""
        return {"summary": self.summary(player), "cost": round(self.cost(player), 4)}


class CountingJev:
    """Wraps the TypeSafe client and records each answered request's tokens."""

    def __init__(self, client, usage: Usage):
        self.client = client
        self.usage = usage

    async def system_one(self, *args, **kwargs):
        response = await self.client.system_one(*args, **kwargs)
        self.usage.record("jev", response.usage.input_tokens, response.usage.output_tokens, charged_to.get())
        return response
