"""What the player overhears of the talk at their place."""

import logging
import random
from collections.abc import Collection

from examples.rumor_mill.actions import CONFRONT, DENY, PREACH, RETELL, SPEAK
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.town import NEWCOMER

log = logging.getLogger(__name__)

# Speech a bystander can overhear; warnings already show in the feed.
SPOKEN = (RETELL, DENY, CONFRONT, SPEAK, PREACH)
HEATED_ACTIONS = (CONFRONT, DENY)
HEATED_STYLES = ("mock", "shift_blame")
LIVELY_STYLES = ("exaggerated", "delighted", "worried", "conspiratorial", "add_detail")


def audible(u: Utterance) -> bool:
    """Whether the line is speech a bystander can hear, not a whisper or a warning."""
    return u.speaker != NEWCOMER and u.action in SPOKEN


class Overhear:
    def __init__(self, contex: ContexClient, baselines: Baselines, config: Config, rng: random.Random):
        self.contex = contex
        self.baselines = baselines
        self.config = config
        self.rng = rng
        self.project = f"rumor-mill:{config.session}:goals"
        self._evidence: dict[str, str] = {}

    async def publish(self, goals: list[Goal]) -> None:
        """Publish each belief goal's evidence words, for matching overheard lines against."""
        for goal in goals:
            if goal.evidence:
                key = f"goal:{goal.id}"
                self._evidence[key] = goal.evidence[0]
                await self.contex.publish(self.project, key, {"id": goal.id, "text": goal.evidence[0]})

    async def keep(
        self, utterance: Utterance, player_place: str, hop: bool = False, goals: Collection[str] = (),
        listening: bool = False,
    ) -> str | None:
        """Why the player overhears the line (listen, heated, lively, goal, or random), or None when they don't.

        A listening player hears every such line. A line counts as goal talk only for the player's own goals, given by id.
        Only speech at the player's place that is not their own whisper or a hop on a rumor card is overheard.
        """
        u = utterance
        if hop or u.place != player_place or not audible(u):
            return None
        if listening:
            return "listen"
        if u.action in HEATED_ACTIONS or u.style in HEATED_STYLES:
            return "heated"
        lively = u.style in LIVELY_STYLES
        if lively and self.rng.random() < self.config.lively_rate:
            return "lively"
        if await self._touches_a_goal(u, goals):
            return "goal"
        if not lively and self.rng.random() < self.config.overhear_rate:
            return "random"
        return None

    async def _touches_a_goal(self, u: Utterance, goals: Collection[str]) -> bool:
        keys = {f"goal:{g}" for g in goals} & self._evidence.keys()
        if not keys:
            return False
        try:
            found = await self.contex.query(self.project, u.text, top_k=len(self._evidence), threshold=0)
            return any(
                m.data_key in keys and m.similarity is not None
                and self.baselines.matches(self._evidence[m.data_key], m.similarity)
                for m in found
            )
        except Exception:
            log.warning("goal check for %s failed", u.id)
            log.debug("goal check failure", exc_info=True)
            return False
