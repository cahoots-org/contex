"""How believable a claim is to an ordinary villager, as a band from 0 (far-fetched) to 3 (plausible)."""

import logging

from typesafe_sdk import Score

log = logging.getLogger(__name__)

LEVELS = ["far-fetched", "unlikely", "believable", "plausible"]
INSTRUCTIONS = "How believable is this claim to an ordinary villager who hears it from a stranger?"
MIDDLING = 2


class Believability:
    """Jev's rating of claims, one ask per claim."""

    def __init__(self, jev):
        self.jev = jev
        # ponytail: grows per claim for the life of the server; prune with the judge's _forget.
        self.bands: dict[str, int] = {}

    async def band(self, claim: str) -> int:
        """The claim's band, 0 to 3, or a middling one if Jev cannot say."""
        if claim in self.bands:
            return self.bands[claim]
        try:
            response = await self.jev.system_one(
                state={"claim": claim}, questions={"q": Score(instructions=INSTRUCTIONS, criteria=LEVELS)},
            )
            band = min(3, int(response.scores["q"].score * 4))
        except Exception:
            log.warning("could not rate the claim %r", claim, exc_info=True)
            return MIDDLING
        self.bands[claim] = band
        return band
