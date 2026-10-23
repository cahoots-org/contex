"""Each player's hint lines, kept in their own Contex project so a draft can be matched against them."""

import itertools
import logging

from examples.whispers.config import Config
from examples.whispers.contex_client import ContexClient

log = logging.getLogger(__name__)

HINT_TOP_K = 20


class Hints:
    def __init__(self, contex: ContexClient, config: Config):
        self.contex = contex
        self.config = config
        self._keys = itertools.count(1)
        self._hinted: set[str] = set()

    def project(self, player: str) -> str:
        """The player's hints project."""
        return f"whispers:{self.config.session}:hints:{player}"

    async def add(self, player: str, villager_id: str, line: str) -> None:
        """Keep a hint line the villager gave the player; a failure is logged."""
        self._hinted.add(player)
        try:
            await self.contex.publish(self.project(player), f"hint:{next(self._keys)}", {"id": villager_id, "text": line})
        except Exception:
            log.warning("hint from %s for %s was not published", villager_id, player)
            log.debug("hint publish failure", exc_info=True)

    async def matching(self, player: str, draft: str) -> list[str]:
        """The ids of villagers whose hints to the player are close to the draft, best first; none on failure."""
        if player not in self._hinted:
            return []
        try:
            found = await self.contex.query(
                self.project(player), draft, top_k=HINT_TOP_K, threshold=self.config.draft_floor,
            )
        except Exception:
            log.warning("hint matching for %s failed", player)
            log.debug("hint query failure", exc_info=True)
            return []
        return list(dict.fromkeys(m.data["id"] for m in found))
