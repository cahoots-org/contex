"""The judge: measures how far the village has come to each goal."""

import asyncio
import logging

from typesafe_sdk import Noul

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.mind import group_project, mind_project
from examples.rumor_mill.models import Villager
from examples.rumor_mill.town import Town

log = logging.getLogger(__name__)

NO_QUALIFYING_GROUP_CAP = 0.99


def _text(match: Match) -> str:
    return str(match.data.get("text") or match.data)


async def _yes(jev, state: dict, question: str) -> bool:
    """Whether Jev thinks a yes is more likely than a no."""
    response = await jev.system_one(state=state, questions={"q": Noul(instructions=question)})
    return response.nouls["q"].noul > 0.5


class Judge:
    def __init__(self, contex: ContexClient, jev, town: Town, config: Config, feed):
        self.contex = contex
        self.jev = jev
        self.town = town
        self.config = config
        self.feed = feed
        self.progress: dict[str, float] = {}
        self.met: set[str] = set()
        self._verdicts: dict[tuple[str, str, frozenset[str]], bool] = {}
        self._semaphore = asyncio.Semaphore(config.judge_concurrency)

    async def _believes(self, goal: Goal, villager: Villager) -> bool | None:
        """Whether the villager believes the goal's claim; None if they left or the check failed."""
        async with self._semaphore:
            try:
                project = mind_project(self.config.session, villager.id)
                matches = await self.contex.query(
                    project, goal.evidence[0], top_k=3, threshold=self.config.judge_threshold,
                    max_age_seconds=self.config.memory_seconds,
                )
                if not matches:
                    return False
                key = (goal.id, villager.id, frozenset(m.data_key for m in matches))
                if key not in self._verdicts:
                    state = {
                        "villager": f"{villager.name}, {villager.occupation}. {villager.personality}",
                        "memories": [_text(m) for m in matches],
                    }
                    self._verdicts[key] = await _yes(self.jev, state, goal.believer_question)
                believes = self._verdicts[key]
            except Exception:
                log.exception("judging %s failed for %s", goal.id, villager.id)
                return None
            return believes if villager.id in self.town else None

    async def _group_qualifies(self, goal: Goal, gid: str, members: int) -> bool:
        if members < goal.min_group_members:
            return False
        async with self._semaphore:
            try:
                matches = await self.contex.query(
                    group_project(self.config.session, gid), goal.evidence[0], top_k=1,
                    threshold=self.config.judge_threshold,
                )
                record = next((m for m in matches if m.data_key == f"group:{gid}"), None)
                return record is not None and await _yes(self.jev, {"group": record.data}, goal.creed_question)
            except Exception:
                log.exception("judging %s failed for group %s", goal.id, gid)
                return False

    async def check(self, goal: Goal) -> float:
        """Progress toward the goal, from 0 to 1."""
        if goal.kind == "state":
            return max(0.0, min(1.0, goal.state_check(self.town)))
        self._verdicts = {k: v for k, v in self._verdicts.items() if k[1] in self.town}
        results = await asyncio.gather(*(self._believes(goal, v) for v in self.town.villagers))
        counted = [r for r in results if r is not None]
        if not counted:
            return 0.0
        progress = min(1.0, sum(counted) / len(counted) / goal.target_fraction)
        if goal.creed_question is not None:
            groups = await asyncio.gather(
                *(self._group_qualifies(goal, g.id, len(g.members)) for g in self.town.groups)
            )
            if not any(groups):
                progress = min(progress, NO_QUALIFYING_GROUP_CAP)
        return progress

    async def run(self, goals: list[Goal]) -> None:
        """Check every goal each interval until all have held at full progress."""
        holds = {g.id: 0 for g in goals}
        pending = list(goals)
        interval = self.config.judge_every_days * self.config.day_seconds
        while pending:
            await asyncio.sleep(interval)
            for goal in list(pending):
                try:
                    progress = await self.check(goal)
                except Exception:
                    log.exception("checking %s failed", goal.id)
                    continue
                if int(progress * 100) != int(self.progress.get(goal.id, 0.0) * 100):
                    self.feed.on_progress(goal, progress)
                self.progress[goal.id] = progress
                holds[goal.id] = holds[goal.id] + 1 if progress >= 1.0 else 0
                if holds[goal.id] >= goal.hold_checks:
                    pending.remove(goal)
                    self.met.add(goal.id)
                    self.feed.on_met(goal)
