"""The judge: measures how far the village has come to each goal in play, and deals new ones."""

import asyncio
import logging
import random
import time
from collections import Counter

from typesafe_sdk import Noul

from examples.rumor_mill import goals
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.game import Game
from examples.rumor_mill.goal_maker import GoalMaker
from examples.rumor_mill.goals import (
    Goal, Trophy, audience_of, claim, deal, goal_counts, kind_of, needed, trophy_line, void_reason, world_goal,
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
        engine=None, maker: GoalMaker | None = None,
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
        self.engine = engine
        self.maker = maker
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
        self._players: Players | None = None
        # By player id, the task writing their next hand.
        self._dealing: dict[str, asyncio.Task] = {}
        # The ids of players waiting on a swapped-in goal.
        self._swapping: set[str] = set()

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

    async def _trace(self, goal: Goal) -> None:
        """Tell the engine of each memory the goal's subject holds that descends from the owner's whisper."""
        if self.engine is None or self.lineage is None or not goal.owner or goal.subject not in self.town:
            return
        async with self._semaphore:
            try:
                matches = await self.contex.query(
                    mind_project(self.config.session, goal.subject), goal.evidence[0], top_k=3,
                    threshold=self.baselines.threshold(goal.evidence[0]), since=self.game and self.game.memory_since(),
                )
            except Exception:
                log.exception("tracing %s to its subject failed", goal.id)
                return
        for m in matches:
            uid = m.data_key.removeprefix("utt:")
            if self.lineage.player_of(uid) == goal.owner:
                self.engine.traced(goal, goal.subject, uid)

    def _wary(self, vid: str, goal: Goal) -> bool:
        """Whether the villager suspects the goal's owner too much to be won over for it."""
        return (
            goal.owner is not None and self.engine is not None
            and self.engine.suspicion.score(vid, goal.owner) >= self.config.warn_threshold
        )

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
        audience = audience_of(goal, self.town)
        results, _ = await asyncio.gather(
            asyncio.gather(*(self._believes(goal, v, roots, doubters) for v in audience)), self._trace(goal),
        )
        for i, v in enumerate(audience):
            if results[i] and self._wary(v.id, goal):
                del roots[v.id]
                doubters[v.id] = None
                results[i] = False
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
        """Give the player three new deck goals, none held now nor the world goal's claim."""
        self._hand_to(player, deal(self.rng, skip=self.held(), never={self.world.id.removeprefix("world-")}))

    async def redeal(self, player: Player) -> None:
        """Give the player a new hand written for them, or from the deck without a goal maker.

        A hand that lands after the player's game ended is dropped, with its generated evidence.
        """
        if self.maker is None:
            self.deal(player)
            return
        others = [g for p in self._players or () if p is not player and not p.over() for g in p.goals]
        taken = {c.lower() for g in (*others, self.world) if (c := claim(g))}
        hand = await self.maker.hand(
            player.id, taken=taken, deck_skip=self.held(), never={self.world.id.removeprefix("world-")},
        )
        if player.over():
            self.maker.overhear.drop([g.id for g in hand if g.owner])
            return
        self._hand_to(player, hand)

    def _hand_to(self, player: Player, hand: list[Goal]) -> None:
        player.goals = hand
        player.met = set()
        counts = {g.id: c for g in player.goals if (c := self.counts_of(g)) is not None}
        self.feed.on_deal(player.goals, player=player.id, counts=counts)

    def _problem_swapping(self, player: Player, goal_id: str) -> str | None:
        """Why the player cannot swap the goal now, or None if they can."""
        goal = next((g for g in player.goals if g.id == goal_id), None)
        if player.over():
            return "The game is over."
        if player.id in self._dealing:
            return "Your next goals are on their way."
        if player.id in self._swapping:
            return "Already finding you a new goal."
        if goal is None:
            return "That isn't one of your goals."
        if goal.id in player.met:
            return "That goal is already met."
        return None

    async def swap(self, player: Player, goal_id: str) -> str | None:
        """Swap the player's unmet goal for a new one of the same kind; returns why it could not be, if it couldn't."""
        if problem := self._problem_swapping(player, goal_id):
            return problem
        old = next(g for g in player.goals if g.id == goal_id)
        never = {self.world.id.removeprefix("world-")}
        skip = self.held() | {g.id for g in player.goals}
        self._swapping.add(player.id)
        try:
            if self.maker is None or (kind := kind_of(old)) is None:
                new = deal(self.rng, 1, skip=skip, never=never)[0]
            else:
                others = [g for p in self._players or () if p is not player and not p.over() for g in p.goals]
                taken = {c.lower() for g in (*others, *player.goals, self.world) if (c := claim(g))}
                new = (await self.maker.hand(player.id, taken, skip, never, kinds=(kind,), size=1))[0]
        finally:
            self._swapping.discard(player.id)
        if player.over() or old not in player.goals or old.id in player.met:
            if new.owner:
                self.maker.overhear.drop([new.id])
            return "That goal changed. Try again."
        if self.maker is not None and old.owner:
            self.maker.overhear.drop([old.id])
        player.goals[player.goals.index(old)] = new
        self.feed.on_goal_swapped(old, new, player=player.id, counts=self.counts_of(new))
        return None

    def _replace_void(self, player: Player) -> None:
        """Swap each of the player's unmet goals that can no longer be met for a deck goal, and tell the player."""
        for i, old in enumerate(player.goals):
            reason = void_reason(old, self.town)
            if reason is None or old.id in player.met:
                continue
            skip = self.held() | {g.id for g in player.goals}
            new = deal(self.rng, 1, skip=skip, never={self.world.id.removeprefix("world-")})[0]
            player.goals[i] = new
            self.feed.on_goal_void(old, new, reason, player=player.id, counts=self.counts_of(new))

    def counts_of(self, goal: Goal) -> tuple[int, int] | None:
        """The goal's believers and how many it needs, or None for a state goal."""
        return goal_counts(goal, self.counts, len(self.town.villagers))

    def _next_world(self) -> Goal:
        held = self.held()
        fresh = [g for g in goals.WORLD_CANDIDATES if g.id not in held]
        return world_goal(self.rng.choice(fresh or goals.WORLD_CANDIDATES))

    async def run(self, players: Players) -> None:
        """Check each goal in play once an interval, telling its holders, until cancelled.

        A player whose goals are all met wins a trophy and is dealt new ones; a met world goal gives a trophy to
        everyone who whispered while it was in play, and is replaced by a new one.
        """
        holds: dict[str, int] = {}
        self._players = players
        interval = self.config.judge_every_days * self.config.day_seconds
        while True:
            await asyncio.sleep(interval)
            if not players.any_playing():
                continue
            self._note_whisperers(players)
            playing = [p for p in players if not p.over()]
            # A player whose next hand is being written has won this one, so it is no longer judged.
            judged = [p for p in playing if p.id not in self._dealing]
            for p in judged:
                self._replace_void(p)
            in_play = {g.id: g for p in judged for g in p.goals}
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
                holders = [p for p in judged if p.id not in self._dealing and any(g.id == goal.id for g in p.goals)]
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
                            trophy = Trophy(
                                "personal", tuple(g.text for g in p.goals), self._day(),
                                long_shot=any(g.long_shot for g in p.goals),
                            )
                            line = trophy_line(trophy, _last_claim(p, goal), unseen=self._unseen(p))
                            players.award(p.owner, trophy, line)
                            self._start_redeal(p)
                if world:
                    self.feed.on_met(goal)
                    self._note_whisperers(players)
                    trophy = Trophy("world", (goal.text,), self._day(), long_shot=goal.long_shot)
                    for owner in sorted(self.whisperers):
                        players.award(owner, trophy, trophy_line(trophy, claim(goal)))
                    self.world = self._next_world()
                    self.world_since = time.time()
                    self.whisperers = set()
                    self.feed.on_world(self.world, self.counts_of(self.world))
            self._forget([g for p in playing for g in p.goals] + [self.world], holds)

    def _unseen(self, player: Player) -> str | None:
        """The name of the first subject among the player's goals whose suspicion of them stayed below the warn line."""
        if self.engine is None:
            return None
        for g in player.goals:
            if g.subject and self.engine.suspicion.score(g.subject, player.id) < self.config.warn_threshold:
                return self.town.name_of(g.subject)
        return None

    def _start_redeal(self, player: Player) -> None:
        """Write the player's next hand in the background, unless it is already being written."""
        if player.id in self._dealing:
            return
        task = asyncio.create_task(self.redeal(player))
        self._dealing[player.id] = task
        task.add_done_callback(lambda done: self._redealt(player, done))

    def _redealt(self, player: Player, task: asyncio.Task) -> None:
        """Stop tracking the player's finished redeal; if it failed, deal them a deck hand instead."""
        self._dealing.pop(player.id, None)
        if task.cancelled() or task.exception() is None or player.over():
            return
        log.error("redealing %s failed", player.id, exc_info=task.exception())
        self.deal(player)

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

    def _forget(self, in_play: list[Goal], holds: dict[str, int]) -> None:
        """Drop what is known of goals no longer in play, so a claim dealt again starts over."""
        ids = {g.id for g in in_play}
        for known in (self.progress, self.counts, self.roots, self.doubt, holds):
            for gid in known.keys() - ids:
                del known[gid]
        questions = {g.believer_question for g in in_play}
        self._verdicts = {k: v for k, v in self._verdicts.items() if k[0] in questions}
        # A hand being written has published evidence for goals not yet in play.
        if self.maker is not None and not self._dealing and not self._swapping:
            self.maker.overhear.drop(self.maker.overhear.generated_ids() - ids)

    def percents(self, goals: list[Goal]) -> list[tuple[str, int]]:
        """Each goal's text and whole percent progress."""
        return [(g.text, int(self.progress.get(g.id, 0.0) * 100)) for g in goals]
