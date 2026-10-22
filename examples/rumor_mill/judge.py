"""The judge: measures how far the village has come to each goal in play, and deals new ones."""

import asyncio
import logging
import random
import time
from collections import Counter

from typesafe_sdk import Noul

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.game import Game
from examples.rumor_mill.goals import (
    WORLD_CANDIDATES, Goal, Trophy, claim, deal, goal_counts, needed, trophy_line, world_goal,
)
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.mind import group_project, mind_project
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import Player
from examples.rumor_mill.players import Players
from examples.rumor_mill.town import Town

log = logging.getLogger(__name__)

NO_QUALIFYING_GROUP_CAP = 0.99


def _text(match: Match) -> str:
    return str(match.data.get("text") or match.data)


def _best(matches: list[Match]) -> Match:
    return max(matches, key=lambda m: m.similarity or 0.0)


async def _yes(jev, state: dict, question: str) -> bool:
    """Whether Jev thinks a yes is more likely than a no."""
    response = await jev.system_one(state=state, questions={"q": Noul(instructions=question)})
    return response.nouls["q"].noul > 0.5


def _last_claim(player: Player, last: Goal) -> str | None:
    """The claim of the goal just met, or else of another belief goal in the player's hand."""
    for goal in (last, *reversed(player.goals)):
        if claim(goal) is not None:
            return claim(goal)
    return None


class Judge:
    def __init__(
        self, contex: ContexClient, jev, town: Town, config: Config, feed, game: Game | None = None, baselines=None,
        rng: random.Random | None = None, lineage: Lineage | None = None, facts: Facts | None = None,
    ):
        self.contex = contex
        self.jev = jev
        self.town = town
        self.config = config
        self.feed = feed
        self.game = game
        self.baselines = baselines
        self.rng = rng or random.Random()
        self.lineage = lineage
        self.facts = facts or Facts()
        self.progress: dict[str, float] = {}
        self.counts: dict[str, tuple[int, int]] = {}
        # By goal id, each believer's id and the player whisper behind their best-matching memory, if one is.
        self.roots: dict[str, dict[str, str | None]] = {}
        # By goal id, how many heard the claim but don't believe it, and what the closest of them is saying.
        self.doubt: dict[str, dict] = {}
        self.world = self._next_world()
        self.world_since = time.time()
        # The owners who whispered while the world goal was in play.
        self.whisperers: set[str] = set()
        self._verdicts: dict[tuple[str, str, frozenset[str]], bool] = {}
        self._semaphore = asyncio.Semaphore(config.judge_concurrency)

    async def _believes(
        self, goal: Goal, villager: Villager, roots: dict[str, str | None], doubters: dict[str, Match | None],
    ) -> bool | None:
        """Whether the villager believes the goal's claim; None if they left or the check failed.

        A believer goes into roots with the player whisper behind their best-matching memory, if one is; a villager
        with matching memories who doesn't believe goes into doubters with their best-matching memory that is not a
        player's own whisper, if one is.
        """
        async with self._semaphore:
            try:
                project = mind_project(self.config.session, villager.id)
                matches = await self.contex.query(
                    project, goal.evidence[0], top_k=3, threshold=self.baselines.threshold(goal.evidence[0]),
                    since=self.game and self.game.memory_since(),
                )
                if not matches:
                    return False
                key = (goal.believer_question, villager.id, frozenset(m.data_key for m in matches))
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
            if villager.id not in self.town:
                return None
            if believes:
                roots[villager.id] = self._root(_best(matches))
            else:
                heard = [m for m in matches if not (self.lineage and self.lineage.is_whisper(m.data.get("id")))]
                doubters[villager.id] = _best(heard) if heard else None
            return believes

    def _words(self, memory: Match) -> str:
        """What the remembered line says, without who said it while the game still keeps it."""
        fact = self.facts.get(memory.data.get("id"))
        return fact.text if fact else _text(memory)

    def _root(self, memory: Match) -> str | None:
        """The player whisper the memory descends from, or None."""
        uid = memory.data_key.removeprefix("utt:")
        if self.lineage is None or self.lineage.player_of(uid) is None:
            return None
        return self.lineage.root(uid)

    async def _group_qualifies(self, goal: Goal, gid: str, members: int) -> bool:
        if members < goal.min_group_members:
            return False
        async with self._semaphore:
            try:
                matches = await self.contex.query(
                    group_project(self.config.session, gid), goal.evidence[0], top_k=1,
                    threshold=0,
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
        roots: dict[str, str | None] = {}
        doubters: dict[str, Match | None] = {}
        results = await asyncio.gather(*(self._believes(goal, v, roots, doubters) for v in self.town.villagers))
        self.roots[goal.id] = roots
        heard = [m for m in doubters.values() if m is not None]
        saying = self._words(_best(heard)) if heard else None
        self.doubt[goal.id] = {"count": len(doubters), "saying": saying}
        counted = [r for r in results if r is not None]
        if not counted:
            return 0.0
        believers, target = sum(counted), needed(goal, len(counted))
        self.counts[goal.id] = (believers, target)
        progress = min(1.0, believers / target)
        if goal.creed_question is not None:
            groups = await asyncio.gather(
                *(self._group_qualifies(goal, g.id, len(g.members)) for g in self.town.groups)
            )
            if not any(groups):
                progress = min(progress, NO_QUALIFYING_GROUP_CAP)
        return progress

    def held(self) -> set[str]:
        """The claims the village holds now, by deck goal id."""
        return {gid.removeprefix("world-") for gid, p in self.progress.items() if p >= 1.0}

    def deal(self, player: Player) -> None:
        """Give the player three new goals, none held now nor the world goal's claim."""
        player.goals = deal(self.rng, skip=self.held(), never={self.world.id.removeprefix("world-")})
        player.met = set()
        counts = {g.id: c for g in player.goals if (c := self.counts_of(g)) is not None}
        self.feed.on_deal(player.goals, player=player.id, counts=counts)

    def counts_of(self, goal: Goal) -> tuple[int, int] | None:
        """The goal's believers and how many it needs, or None for a state goal."""
        return goal_counts(goal, self.counts, len(self.town.villagers))

    def _next_world(self) -> Goal:
        held = self.held()
        fresh = [g for g in WORLD_CANDIDATES if g.id not in held]
        return world_goal(self.rng.choice(fresh or WORLD_CANDIDATES))

    async def run(self, players: Players) -> None:
        """Check each goal in play once an interval, telling its holders, until cancelled.

        A player whose goals are all met wins a trophy and is dealt new ones; a met world goal gives a trophy to
        everyone who whispered while it was in play, and is replaced by a new one.
        """
        holds: dict[str, int] = {}
        interval = self.config.judge_every_days * self.config.day_seconds
        while True:
            await asyncio.sleep(interval)
            if not players.any_playing():
                continue
            self._note_whisperers(players)
            playing = [p for p in players if not p.over()]
            in_play = {g.id: g for p in playing for g in p.goals}
            in_play[self.world.id] = self.world
            for goal in in_play.values():
                before = dict(self.roots.get(goal.id, {}))
                doubted = self.doubt.get(goal.id, {}).get("count")
                try:
                    progress = await self.check(goal)
                except Exception:
                    log.exception("checking %s failed", goal.id)
                    continue
                world = goal.id == self.world.id
                holders = [p for p in playing if any(g.id == goal.id for g in p.goals)]
                doubt = self.doubt.get(goal.id)
                moved = int(progress * 100) != int(self.progress.get(goal.id, 0.0) * 100)
                if moved or self.doubt.get(goal.id, {}).get("count") != doubted:
                    counts = self.counts_of(goal)
                    for p in holders:
                        self.feed.on_progress(
                            goal, progress, counts, player=p.id, rumor=self._moved(goal.id, p.id, before), doubt=doubt,
                        )
                    if world:
                        self.feed.on_progress(goal, progress, counts, doubt=doubt)
                self.progress[goal.id] = progress
                holds[goal.id] = holds.get(goal.id, 0) + 1 if progress >= 1.0 else 0
                if holds[goal.id] < goal.hold_checks:
                    continue
                for p in holders:
                    if goal.id not in p.met:
                        p.met.add(goal.id)
                        self.feed.on_met(goal, player=p.id)
                        if all(g.id in p.met for g in p.goals):
                            trophy = Trophy("personal", tuple(g.text for g in p.goals), self._day())
                            players.award(p.owner, trophy, trophy_line(trophy, _last_claim(p, goal)))
                            self.deal(p)
                if world:
                    self.feed.on_met(goal)
                    self._note_whisperers(players)
                    trophy = Trophy("world", (goal.text,), self._day())
                    for owner in sorted(self.whisperers):
                        players.award(owner, trophy, trophy_line(trophy, claim(goal)))
                    self.world = self._next_world()
                    self.world_since = time.time()
                    self.whisperers = set()
                    self.feed.on_world(self.world, self.counts_of(self.world))
            self._forget({g.id for p in playing for g in p.goals} | {self.world.id}, holds)

    def _moved(self, goal_id: str, player_id: str, before: dict[str, str | None]) -> str | None:
        """The player's whisper that gained the goal the most believers since before, or None if none gained any."""
        gained = Counter(
            root for vid, root in self.roots.get(goal_id, {}).items()
            if root is not None and before.get(vid) != root and self.lineage.player_of(root) == player_id
        )
        return gained.most_common(1)[0][0] if gained else None

    def _note_whisperers(self, players: Players) -> None:
        """Remember the owner of each player who has whispered since the world goal was drawn."""
        self.whisperers |= {
            p.owner for p in players if p.whispered_at is not None and p.whispered_at >= self.world_since
        }

    def _day(self) -> int:
        return self.game.day if self.game else 0

    def _forget(self, in_play: set[str], holds: dict[str, int]) -> None:
        """Drop what is known of goals no longer in play, so a claim dealt again starts over."""
        for known in (self.progress, self.counts, self.roots, self.doubt, holds):
            for gid in known.keys() - in_play:
                del known[gid]

    def percents(self, goals: list[Goal]) -> list[tuple[str, int]]:
        """Each goal's text and whole percent progress."""
        return [(g.text, int(self.progress.get(g.id, 0.0) * 100)) for g in goals]
