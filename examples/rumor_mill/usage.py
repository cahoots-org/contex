"""Token counts per provider and what they cost at the configured prices."""

from examples.rumor_mill.config import Config

PROVIDERS = ("haiku", "jev")


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


class Usage:
    def __init__(self, config: Config):
        self.prices = {
            "haiku": (config.haiku_input_per_mtok, config.haiku_output_per_mtok),
            "jev": (config.jev_input_per_mtok, config.jev_output_per_mtok),
        }
        self.tokens = {p: [0, 0] for p in PROVIDERS}

    def record(self, provider: str, input_tokens: int | None, output_tokens: int | None) -> None:
        counts = self.tokens[provider]
        counts[0] += input_tokens or 0
        counts[1] += output_tokens or 0

    def cost(self) -> float:
        """Estimated dollars spent so far."""
        return sum(
            (inp * self.prices[p][0] + out * self.prices[p][1]) / 1_000_000
            for p, (inp, out) in self.tokens.items()
        )

    def summary(self) -> str:
        parts = []
        for name, (inp, out) in (("Haiku", self.tokens["haiku"]), ("Jev", self.tokens["jev"])):
            if inp or out:
                parts.append(f"{name} {_tokens(inp)} in" + (f" / {_tokens(out)} out" if out else ""))
        detail = f" ({', '.join(parts)})" if parts else ""
        cost = self.cost()
        dollars = "less than $0.01" if 0 < cost < 0.005 else f"${cost:.2f}"
        return f"Estimated cost so far: {dollars}{detail}"

    def to_json(self) -> dict:
        return {"summary": self.summary(), "cost": round(self.cost(), 4)}


class CountingJev:
    """Wraps the TypeSafe client and records each answered request's tokens."""

    def __init__(self, client, usage: Usage):
        self.client = client
        self.usage = usage

    async def system_one(self, *args, **kwargs):
        response = await self.client.system_one(*args, **kwargs)
        self.usage.record("jev", response.usage.input_tokens, response.usage.output_tokens)
        return response
