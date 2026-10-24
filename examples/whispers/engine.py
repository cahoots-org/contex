"""The engine: one worker per villager, woken only by Contex pushes."""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Sequence
from contextlib import aclosing, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from examples.whispers.actions import ANSWER, LEAVE_TOWN, MOVE, WARN
from examples.whispers.calibration import Baselines
from examples.whispers.config import Config
from examples.whispers.contex_client import ContexClient, Match
from examples.whispers.decide import Decision, TurnView, decide
from examples.whispers.effects import act
from examples.whispers.facts import Facts
from examples.whispers.feed import MOMENTS
from examples.whispers.game import PHASES, Game, Summary
from examples.whispers.goals import Goal, audience_of
from examples.whispers.hints import Hints
from examples.whispers.lineage import Ids, Lineage
from examples.whispers.mind import Mind, group_project, mind_project, said_project
from examples.whispers.models import Group, Utterance, Villager
from examples.whispers.overhear import Overhear
from examples.whispers.player import Player
from examples.whispers.players import Players
from examples.whispers.pressure import Pressure
from examples.whispers.rapport import Rapport, taken_words
from examples.whispers.routine import Routine
from examples.whispers.rumors import Rumors
from examples.whispers.scenes import SCENE_SPEECH, Scene, Scenes
from examples.whispers.suspicion import Suspicion, influence
from examples.whispers.town import START_PLACE, Town
from examples.whispers.usage import charging
from examples.whispers.writer import WHISPER_FRAMING, Writer

log = logging.getLogger(__name__)

LISTEN_RETRY_SECONDS = 1
# How many keys that reached a turn are remembered per villager, so neither a poll nor a direct hit hears them again.
CONSUMED_KEYS = 500
# Reach counts that mark a moment as a rumor spreads.
MOMENT_SIZES = (5, 10)
# How many whispers a villager kept to themselves are watched for a later relay.
KEPT_WHISPERS = 50
# How many villagers set off together on their daily round, and how much of each part of the day the round spans.
ROUTINE_BATCH = 5
ROUTINE_SPREAD = 0.5
# Slack under pressure_leave so pressure that adds up to the mark in floating point still counts as reaching it.
LEAVE_TOLERANCE = 1e-9
# How much of a whisper's suspicion a subject takes on, by hops from the whisper.
TRACE_SCALE = {1: 1, 2: 0.5, 3: 0.25}
MEETING = "The village meets in the square and runs the newcomer out of town."


class Engine:
    def __init__(
        self,
        config: Config,
        town: Town,
        contex: ContexClient,
        jev,
        writer: Writer,
        lineage: Lineage,
        ids: Ids,
        feed,
        mind_factory: Callable[[Villager], Mind] | None = None,
        game: Game | None = None,
        summary_source: Callable[[Player], list[tuple[str, int]]] = lambda p: [],
        baselines: Baselines | None = None,
        rumors: Rumors | None = None,
        overhear: Overhear | None = None,
        routine: Routine | None = None,
        facts: Facts | None = None,
    ):
        self.config = config
        self.town = town
        self.contex = contex
        self.jev = jev
        self.writer = writer
        self.lineage = lineage
        self.ids = ids
        self.feed = feed
        self.facts = facts or Facts()
        self.hints = Hints(contex, config)
        self.suspicion = Suspicion(config, town)
        self.rapport = Rapport(config)
        self.pressure = Pressure(config)
        self.scenes = Scenes(config, town, random.Random())
        self.baselines = baselines
        self.rumors = rumors
        self.overhear = overhear
        self.routine = routine
        self.players = Players(feed)
        # The judge told of each action a player's rumor drove, set once it exists.
        self.judge = None
        self.mind_factory = mind_factory or (lambda v: Mind(contex, v, config, baselines))
        self.game = game
        self.summary_source = summary_source
        self.minds: dict[str, Mind] = {}
        self.departures: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._read_at: dict[str, float] = {}
        self._wakes: dict[str, asyncio.Event] = {}
        self._pending: dict[str, list[Match]] = {}
        self._last_turn: dict[str, float] = {}
        self._turning: set[str] = set()
        # Villagers on a routine move, whose carried news counts as said like a turn's.
        self._strolling: set[str] = set()
        self._consumed: dict[str, dict[str, None]] = {}
        self._listen_failed: set[str] = set()
        self._said: dict[str, list[tuple[str, int]]] = {}
        self._kept: dict[str, dict[str, None]] = {}
        # How readily each villager passes news on, asked once per villager.
        self._gossip: dict[str, float] = {}
        # Each open conversation, keyed by (villager id, player id): its first whisper and the whispers answered so far.
        self.conversations: dict[tuple[str, str], dict] = {}
        self._renewal: asyncio.Task | None = None
        self._checks: asyncio.Task | None = None
        self._clock: asyncio.Task | None = None
        self._walking: asyncio.Task | None = None
        # The last new item of each villager's last turn, which a routine move may carry.
        self._latest: dict[str, Match] = {}
        # Villagers whose own turn moved them this part of the day, so the round leaves them be.
        self._chose_move: set[str] = set()
        self._turns = asyncio.Semaphore(config.turn_concurrency)
        # Care checks on the listeners of one-to-one rumor hops.
        self._following: set[asyncio.Task] = set()
        # The share of a whisper's suspicion each (subject, player, rumor root) has taken on, the goals whose subject has denied, and the denials running.
        # ponytail: _traced and _denied grow per rumor root and goal for the life of the server; prune with the judge's _forget.
        self._traced: dict[tuple[str, str, str], float] = {}
        self._denied: set[str] = set()
        self._denials: set[asyncio.Task] = set()
        if rumors is not None:
            feed.sinks.append(rumors.on_event)

    async def add_villager(self, v: Villager, place: str = START_PLACE) -> None:
        mind = self.mind_factory(v)
        await mind.open()
        self.town.add(v, place)
        self.minds[v.id] = mind
        self._read_at[v.id] = time.monotonic()
        wake = self._wakes[v.id] = asyncio.Event()
        self._tasks[v.id] = [
            asyncio.create_task(self._listen(mind, wake)),
            asyncio.create_task(self._work(mind, wake)),
        ]

    async def remove_villager(self, vid: str) -> None:
        """Stop the villager's tasks; the subscription is left to expire."""
        if self.minds.pop(vid, None) is not None:
            self.departures.put_nowait(vid)
        self._read_at.pop(vid, None)
        self._wakes.pop(vid, None)
        self._pending.pop(vid, None)
        self._last_turn.pop(vid, None)
        self._turning.discard(vid)
        self._consumed.pop(vid, None)
        self._listen_failed.discard(vid)
        self._said.pop(vid, None)
        self._kept.pop(vid, None)
        self._latest.pop(vid, None)
        self._chose_move.discard(vid)
        self.pressure.drop(vid)
        self._leave_scene(vid)
        self.end_conversation(vid)
        for wid, name in self.feed.listening(vid):
            self.feed.on_whisper_status(wid, vid, name, "kept")
        with suppress(KeyError):
            self.town.remove(vid)
        current = asyncio.current_task()
        tasks = [t for t in self._tasks.pop(vid, []) if t is not current]
        await _cancel(tasks)

    async def run(self) -> None:
        """Start lease renewal and, with a game, the town check; returns at once."""
        self._renewal = asyncio.create_task(self._renew_loop())
        if self.game is not None:
            self._checks = asyncio.create_task(self._suspicion_loop())
            self._clock = asyncio.create_task(self._clock_loop())

    async def stop(self) -> None:
        tasks = [t for ts in self._tasks.values() for t in ts]
        tasks += [t for t in (self._renewal, self._checks, self._clock, self._walking) if t]
        tasks += self._following
        self._tasks.clear()
        self._renewal = self._checks = self._clock = self._walking = None
        if self.rumors is not None:
            self.rumors.close()
        await _cancel(tasks)

    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None:
        """Publish the utterance to each listener's mind; raises only if every listener failed.

        An addressed utterance also wakes each listener and joins their next turn unmatched. A line that goes on
        with a scene is heard by those it addresses too, and wakes them while it stays on the scene's topic. Each player
        overhears every such line of a village-talk scene where they stand.
        """
        scene = self.scenes.speaking(utterance)
        drawn = self.scenes.addressed(scene, utterance.speaker) if scene else []
        listener_ids = [*listener_ids, *(i for i in drawn if i not in listener_ids)]
        listeners = [i for i in listener_ids if i in self.town and i in self.minds]
        data = utterance.to_data()
        hearing = [self.minds[i].hear(f"utt:{utterance.id}", data) for i in listeners]
        if utterance.speaker in self.minds:
            hearing.append(self._remember_said(utterance.speaker, f"said:{utterance.id}", data))
        results = await asyncio.gather(*hearing, return_exceptions=True)
        for vid, result in zip(listeners, results):
            if isinstance(result, Exception):
                log.error("delivery to %s failed", vid, exc_info=result)
        heard = [vid for vid, result in zip(listeners, results) if not isinstance(result, Exception)]
        if addressed:
            for vid in heard:
                self.hear_directly(vid, Match(f"utt:{utterance.id}", data))
        if listeners and not heard:
            raise next(r for r in results if isinstance(r, Exception))
        on_topic = await self._carries_rumor(utterance, heard)
        if heard and on_topic and (utterance.speaker in self._turning or utterance.speaker in self._strolling):
            self._said.setdefault(utterance.speaker, []).append((utterance.id, len(heard)))
        if scene is not None:
            if self.lineage.root(utterance.id) != scene.rumor:
                in_scene = await self.rumors.on_topic(scene.topic, utterance.text)
            else:
                in_scene = on_topic
            if scene.ended:
                scene = None
            elif in_scene:
                self.scenes.said(scene, utterance.id)
                for vid in drawn:
                    if vid in heard:
                        self.hear_directly(vid, Match(f"utt:{utterance.id}", data))
            else:
                self._end_scene(scene, "veered")
                scene = None
        self.feed.on_delivery(utterance, heard, on_topic, scene=scene and scene.id)
        if scene is not None and scene.rumor is None:
            for player in self.players.at(scene.place):
                self.feed.on_overheard(utterance, "scene", scene.id, player=player.id)
        elif self.overhear is not None:
            for player in self.players.at(utterance.place):
                hop = on_topic and self.lineage.player_of(utterance.id) == player.id
                try:
                    reason = await self.overhear.keep(
                        utterance, player.place, hop, goals=[g.id for g in player.goals], listening=player.listening(),
                    )
                except Exception:
                    log.warning("overhearing %s failed", utterance.id)
                    log.debug("overhearing failure", exc_info=True)
                else:
                    if reason:
                        self.feed.on_overheard(utterance, reason, player=player.id)

    async def reply(self, utterance: Utterance) -> None:
        """Remember a villager's answer to the newcomer and show it on its conversation; no mind hears it."""
        await self._remember_said(utterance.speaker, f"said:{utterance.id}", utterance.to_data())
        whisper = self.facts.get(utterance.parent)
        conversation = (whisper and whisper.conversation) or utterance.parent
        self.feed.on_reply(conversation, utterance)

    def end_conversation(self, vid: str, player: str | None = None) -> None:
        """Close the villager's conversation with player, or with every player when None; its answers have moved on."""
        keys = [k for k in self.conversations if k[0] == vid and player in (None, k[1])]
        name = self.town.name_of(vid) or vid
        for key in keys:
            for wid in self.conversations.pop(key)["answered"]:
                self.feed.on_whisper_status(wid, vid, name, "moved_on")

    async def _remember_said(self, vid: str, key: str, data: dict) -> None:
        """Keep a line the villager spoke in their said project, which nothing subscribes to."""
        try:
            await self.contex.publish(said_project(self.config.session, vid), key, data)
        except Exception as e:
            log.warning("remembering a line said by %s failed: %s", vid, type(e).__name__)

    async def _recall_said(self, vid: str, lines: list[Match]) -> list[Match]:
        """The villager's earlier lines that the newcomer's words match, best first."""
        found: dict[str, Match] = {}
        for m in lines:
            words = WHISPER_FRAMING.sub("", m.data.get("text", ""))
            try:
                hits = await self.contex.query(
                    said_project(self.config.session, vid), words,
                    top_k=3, since=self.game and self.game.memory_since(), threshold=0,
                )
            except Exception as e:
                log.warning("searching what %s said failed: %s", vid, type(e).__name__)
                continue
            for hit in hits:
                if hit.similarity is not None and hit.similarity >= self.config.said_floor:
                    found.setdefault(hit.data_key, hit)
        return sorted(found.values(), key=lambda h: h.similarity, reverse=True)[:3]

    async def recall_heard(self, vid: str, text: str, exclude: str) -> list[Match]:
        """Lines the villager heard earlier that the newcomer's words match, best first.

        The newcomer's own whispers, this one (exclude) and any before it, are left out.
        """
        try:
            hits = await self.contex.query(
                mind_project(self.config.session, vid), WHISPER_FRAMING.sub("", text),
                top_k=3, since=self.game and self.game.memory_since(), threshold=0,
            )
        except Exception as e:
            log.warning("searching what %s heard failed: %s", vid, type(e).__name__)
            return []
        found = [
            h for h in hits
            if h.similarity is not None and h.similarity >= self.config.heard_floor
            and h.data.get("id") != exclude and not self.lineage.is_whisper(h.data.get("id"))
        ]
        return sorted(found, key=lambda h: h.similarity, reverse=True)[:3]

    async def _recall_heard_all(self, vid: str, whispers: list[Match]) -> list[Match]:
        """What the villager heard before that any of the newcomer's whispers this turn bring back, best first."""
        found: dict[str, Match] = {}
        for m in whispers:
            for hit in await self.recall_heard(vid, m.data.get("text", ""), m.data.get("id")):
                found.setdefault(hit.data_key, hit)
        return sorted(found.values(), key=lambda h: h.similarity, reverse=True)

    async def _carries_rumor(self, utterance: Utterance, heard: list[str]) -> bool:
        """Whether a retelling of a whisper still carries it, recorded on its card; True for any other line."""
        root = self.lineage.root(utterance.id)
        if not heard or self.rumors is None or root == utterance.id or not self.lineage.is_whisper(root):
            return True
        on_topic = await self.rumors.on_topic(root, utterance.text)
        if self.rumors.record(root, utterance, {i: self.town.name_of(i) or i for i in heard}, on_topic):
            self.feed.on_drift(root)
        if on_topic:
            self._moments(root, utterance, heard)
        if on_topic and len(heard) == 1 and self.baselines is not None:
            since = (datetime.now(UTC) - timedelta(seconds=self.config.check_lookback_seconds)).isoformat()
            task = asyncio.create_task(self._follow(root, utterance, heard[0], since))
            self._following.add(task)
            task.add_done_callback(self._following.discard)
        return on_topic

    def _moments(self, root: str, u: Utterance, heard: list[str]) -> None:
        """Mark the turning points an on-topic retelling brought the rumor, each once."""
        card = self.rumors.card(root)
        if card is None:
            return
        for size in MOMENT_SIZES:
            if size <= len(card.heard) and self.rumors.first(root, f"size:{size}"):
                self.feed.on_moment(root, MOMENTS["size"].format(n=size))
        for vid in heard:
            if vid in self.town and (who := influence(self.town.get(vid))) and self.rumors.first(root, f"influence:{vid}"):
                self.feed.on_moment(root, MOMENTS["influence"].format(name=self.town.get(vid).name, influence=who))
        if card.place and u.place != card.place and self.rumors.first(root, f"place:{u.place}"):
            self.feed.on_moment(root, MOMENTS["place"].format(place=u.place))
        if u.names_newcomer and self.rumors.first(root, "turned"):
            self.feed.on_moment(root, MOMENTS["turned"].format(name=u.speaker_name), "bad")

    async def cares(self, vid: str, uid: str, depth: int, since: str) -> str | None:
        """Whether the line uid in vid's mind touches a need: cares, worn (too retold to wake them) or uncared.

        One query per need, as a whisper's check; None when the check fails.
        """
        project = mind_project(self.config.session, vid)
        try:
            needs = self.town.get(vid).needs
            found = await asyncio.gather(*(
                self.contex.query(project, need, top_k=self.config.top_k, since=since, threshold=self.baselines.threshold(need))
                for need in needs
            ))
        except Exception:
            log.warning("care check of %s for %s failed", uid, vid)
            log.debug("care check failure", exc_info=True)
            return None
        key = f"utt:{uid}"
        clearances = [
            m.similarity - self.baselines.threshold(need)
            for need, matches in zip(needs, found) for m in matches if m.data_key == key and m.similarity is not None
        ]
        if not clearances:
            return "uncared"
        log.debug("care check of %s for %s: %.4f", uid, vid, max(clearances))
        return "cares" if max(clearances) >= self.config.fatigue_per_hop * depth else "worn"

    async def _follow(self, root: str, u: Utterance, vid: str, since: str) -> None:
        """Mark on the rumor's frontier a listener who will not hear the hop now: busy, uncared or worn."""
        verdict = await self.cares(vid, u.id, u.depth, since)
        name = self.town.name_of(vid) or vid
        if verdict == "cares":
            until = self.cooldown_ends(vid)
            if until > time.time():
                self.rumors.reached(root, vid, name, "busy", until=until)
        elif verdict in ("uncared", "worn"):
            self.rumors.reached(root, vid, name, verdict, depth=u.depth)

    def _carried(self, uid: str | None, vid: str) -> bool:
        """Whether uid is an on-topic hop shown on a player whisper's card, told to vid alone."""
        if uid is None or self.rumors is None:
            return False
        root = self.lineage.root(uid)
        return root != uid and self.lineage.is_whisper(root) and self.rumors.told_only(root, uid, vid)

    def hear_directly(self, vid: str, match: Match) -> bool:
        """Add the match to the villager's next turn and wake them; False once they have left."""
        if vid not in self.town or vid not in self.minds:
            return False
        pending = self._pending.setdefault(vid, [])
        if all(m.data_key != match.data_key for m in pending):
            pending.append(match)
        self._wakes[vid].set()
        return True

    def cooldown_ends(self, vid: str) -> float:
        """Wall-clock time when the villager can next take a turn; now or earlier when free.

        A villager in a scene waits only the scene cooldown.
        """
        cooldown = self.config.scene_cooldown_seconds if self.scenes.of(vid) else self.config.cooldown_seconds
        if vid in self._turning:
            return time.time() + cooldown
        last = self._last_turn.get(vid)
        if last is None:
            return time.time()
        return time.time() + last + cooldown - time.monotonic()

    def suspect(self, vid: str, player: str, amount: float, reason: str, uid: str | None = None) -> None:
        """Raise the villager's suspicion of player."""
        self.suspicion.bump(vid, player, amount, reason, uid)

    def traced(self, goal: Goal, subject_id: str, uid: str) -> None:
        """The goal's subject holds a memory descending from the owner's whisper: they suspect the owner of it.

        A rumor adds its closest hop's share in all, by hops from the whisper. A subject who crosses into wariness of the owner denies the
        rumor to the goal's audience, once per goal.
        """
        key = (subject_id, goal.owner, self.lineage.root(uid))
        scale = TRACE_SCALE.get(self.lineage.depth(uid) + 1, 0)
        if scale <= self._traced.get(key, 0):
            return
        before = self.suspicion.score(subject_id, goal.owner)
        after = self.suspicion.bump(
            subject_id, goal.owner, (scale - self._traced.get(key, 0)) * self.config.suspicion_whisper_bump,
            "traced it to you", uid,
        )
        self._traced[key] = scale
        if before < self.config.warn_threshold <= after and goal.id not in self._denied:
            self._denied.add(goal.id)
            task = asyncio.create_task(self._deny(goal, subject_id))
            self._denials.add(task)
            task.add_done_callback(self._denials.discard)

    async def _deny(self, goal: Goal, subject_id: str) -> None:
        """The subject tells the goal's marks the rumor is false, and the owner sees them do it."""
        try:
            subject = self.town.get(subject_id)
            with charging(goal.owner):
                line = await self.writer.deny(subject, goal.claim_text)
            utterance = Utterance(
                self.ids.next("u"), subject_id, subject.name, self.town.place_of(subject_id), line, None, "said",
            )
            self.lineage.record(utterance.id, None)
            await self.deliver(utterance, [v.id for v in audience_of(goal, self.town)])
            self.feed.on_denied(subject.name, line, player=goal.owner)
        except Exception:
            log.exception("denial by %s failed", subject_id)

    async def publish_group(self, group: Group) -> None:
        data = {"id": group.id, "name": group.name, "creed": group.creed, "founder": group.founder}
        await self.contex.publish(group_project(self.config.session, group.id), f"group:{group.id}", data)

    async def _listen(self, mind: Mind, wake: asyncio.Event) -> None:
        """Own the push stream for one mind; a push only sets the wake event."""
        while True:
            try:
                async with aclosing(mind.pushes()) as pushes:
                    async for _ in pushes:
                        wake.set()
            except Exception as e:
                vid = mind.villager.id
                if vid in self._listen_failed:
                    log.warning("listen failed again for %s: %r", vid, e)
                else:
                    self._listen_failed.add(vid)
                    log.exception("listen failed for %s", vid)
            await asyncio.sleep(LISTEN_RETRY_SECONDS)

    async def _work(self, mind: Mind, wake: asyncio.Event) -> None:
        """Take at most one turn per cooldown, coalescing the pushes in between."""
        vid = mind.villager.id
        while True:
            await wake.wait()
            await asyncio.sleep(max(0.0, self.cooldown_ends(vid) - time.time()))
            wake.clear()
            self._turning.add(vid)
            try:
                if await self._turn(mind):
                    self._last_turn[vid] = time.monotonic()
            except Exception:
                log.exception("turn failed for %s", vid)
                self._last_turn[vid] = time.monotonic()
                self._leave_scene(vid)
                if self._pending.get(vid):
                    wake.set()
            finally:
                self._turning.discard(vid)
            if vid not in self.town:
                await self.remove_villager(vid)
                return

    async def _turn(self, mind: Mind) -> bool:
        """Poll the mind, add addressed messages, and if anything fresh is new, decide and act.

        Returns whether a turn ran. While nobody is playing, nothing is read and no turn runs.
        """
        if not self.players.any_playing():
            return False
        v = mind.villager
        new = await mind.poll()
        self._read_at[v.id] = time.monotonic()
        consumed = self._consumed.setdefault(v.id, {})
        pending = self._pending.pop(v.id, [])
        pending_keys = {m.data_key for m in pending}
        new = [m for m in new if m.data_key not in consumed and m.data_key not in pending_keys]
        new += [m for m in pending if m.data_key not in consumed]
        for m in new:
            consumed[m.data_key] = None
        while len(consumed) > CONSUMED_KEYS:
            del consumed[next(iter(consumed))]
        stale = [m for m in new if not self._fresh(m)]
        stale_keys = {m.data_key for m in stale}
        new = [m for m in new if m.data_key not in stale_keys]
        if not new:
            return False
        self._latest[v.id] = new[-1]
        keys = {m.data_key for m in new}
        # The newcomer's own whispers among the new items, for whisper outcomes.
        whispers = [m for m in new if self.lineage.is_whisper(m.data.get("id"))]
        said = await self._recall_said(v.id, whispers) if whispers else []
        heard = await self._recall_heard_all(v.id, whispers)
        answerable = [m for m in whispers if self._can_answer(v.id, m.data["id"])]
        place = self.town.place_of(v.id)
        front = {self._about(m) for m in new} - {None}
        suspect = self.suspicion.most_suspected(v.id, front)
        holders = self._holders(v.id)
        view = TurnView(
            villager=v,
            place=place,
            present=self.town.present(place, exclude=v.id),
            member_groups=self.town.member_groups(v.id),
            visible_groups=self.town.visible_groups(v.id),
            new=new,
            recent=stale + [m for m in mind.matches if m.data_key not in keys | stale_keys],
            suspicion=self.suspicion.score(v.id, suspect) if suspect else 0.0,
            warn_threshold=self.config.warn_threshold,
            names_newcomer=[m for m in new if m in whispers or self._names_newcomer(m)],
            said=said,
            heard=heard,
            answerable=answerable,
            ask_gossip=v.id not in self._gossip,
            ask_keen=self.scenes.of(v.id) is not None or any(self._exchange(m, v.id, place) for m in new),
            held=any(not p.annoyed(v.id) for p in holders),
            taken=self._taken(v.id, new),
            breaking=self.pressure.words(v.id),
        )
        action = answered = None
        # The turn answers to the player behind the first of its new items that a player started, if any.
        started = [p for p in map(self._player_behind, new) if p]
        payer = started[0] if started else None
        try:
            async with self._turns:
                with charging(payer):
                    decision = await decide(self.jev, view)
                # An ignored turn has no source; its pressure comes from the latest thing heard.
                pressed_by = decision.source or new[-1]
                forced = self._press(v, pressed_by, decision)
                if forced:
                    decision = replace(decision, action=LEAVE_TOWN, source=pressed_by, target=None)
                action = decision.action
                if decision.suspicion_strength is not None:
                    self._rate(v.id, decision.suspicion_strength, view.names_newcomer)
                if decision.gossip is not None:
                    self._gossip[v.id] = decision.gossip
                if action in (MOVE, LEAVE_TOWN):
                    for p in holders:
                        annoyed = p.annoyed(v.id)
                        if forced or annoyed:
                            await self._storm_off(v, p, suspect=annoyed)
                with charging(self._player_behind(decision.source) or payer):
                    scene, joined = await self._enter_scene(v, decision, place)
                    await act(
                        decision, v, town=self.town, writer=self.writer, hub=self,
                        lineage=self.lineage, ids=self.ids, facts=self.facts,
                        inspiration=[m.data.get("text", "") for m in new],
                        said=[m.data.get("text", "") for m in said],
                        heard=[m.data.get("text", "") for m in heard],
                        suspicion_hear_max=self.config.suspicion_hear_max,
                        share_chance=self.share_chance(v.id, decision.source) if action == MOVE else 0.0,
                        about=suspect,
                        mood=self._mood(v.id, decision) if action == ANSWER else "",
                    )
                if action == MOVE:
                    self._chose_move.add(v.id)
                if scene is not None:
                    self._scene_turn(scene, v.id, decision, joined)
                if action == ANSWER:
                    answered = decision.source.data["id"]
                    if (player := self._player_behind(decision.source)) is not None:
                        self.rapport.greeted(v.id, player, self._rumor_of(answered))
        finally:
            self._report(
                v, [m.data["id"] for m in whispers], action, self._said.pop(v.id, []), answered,
                [m.data["id"] for m in new if self._carried(m.data.get("id"), v.id)],
            )
        return True

    async def _storm_off(self, v: Villager, player: Player, suspect: bool = True) -> None:
        """The villager breaks off with player: parting words to them alone, their exchange ended, and a little
        suspicion when suspect."""
        with charging(player.id):
            try:
                line = await self.writer.storm_off(v) or None
            except Exception:
                log.exception("parting line for %s failed", v.id)
                line = None
        self.feed.on_storm_off(v.id, v.name, line, player=player.id)
        player.part(v.id)
        if suspect:
            self.suspicion.bump(v.id, player.id, self.config.suspicion_whisper_bump / 2, "stormed off")

    def acted(
        self, decision: Decision, vid: str, target: str | None, creed: str | None = None, farewell: str | None = None,
    ) -> None:
        """Tell the judge of the villager's action and the player behind it: for leaving town, the one who pressed
        them most, else the one behind the line that broke them; otherwise the one whose rumor it is."""
        player = self._player_behind(decision.source)
        if decision.action == LEAVE_TOWN:
            player = self.pressure.culprit(vid) or player
            log.info("%s left town, credited to %s", vid, player)
            self.feed.on_departure(vid, self.town.name_of(vid) or vid, farewell)
        if self.judge is not None:
            self.judge.record_action(vid, decision.action, target, player, creed)

    def _press(self, v: Villager, m: Match, decision: Decision) -> bool:
        """Raise the villager's pressure by how much m stung and frightened them, crediting the player behind it;
        returns whether it is enough to drive them out of town."""
        line = self.facts.get(m.data.get("id"))
        tie = self.config.pressure_tie_factor if line is not None and line.speaker in v.relationships else 1.0
        amount = self.config.pressure_per_hearing * ((decision.sting or 0.0) * tie + (decision.fear or 0.0))
        return self.pressure.add(v.id, amount, self._player_behind(m)) >= self.config.pressure_leave - LEAVE_TOLERANCE

    def _rumor_of(self, uid: str) -> str:
        """The key her interest is kept under for the player's whisper behind uid."""
        whisper = self.lineage.root(uid)
        fact = self.facts.get(whisper)
        return fact.conversation if fact and fact.conversation else whisper

    def _interest_in(self, vid: str, uid: str) -> float | None:
        """Her interest in the player's rumor behind uid, or None when no player's whisper is or she only heard it
        second-hand."""
        player = self.lineage.player_of(uid)
        if player is None:
            return None
        rumor = self._rumor_of(uid)
        return self.rapport.interest(vid, player, rumor) if self.rapport.known(vid, player, rumor) else None

    def taken_by(self, vid: str, uid: str) -> str | None:
        """How taken she was with the player's rumor behind uid, in a word, or None."""
        interest = self._interest_in(vid, uid)
        return None if interest is None else taken_words(interest)

    def _taken(self, vid: str, new: list[Match]) -> str | None:
        """How taken she was with the player's rumor behind the new items: the strongest if several."""
        interests = [i for m in new if (i := self._interest_in(vid, m.data.get("id"))) is not None]
        return taken_words(max(interests)) if interests else None

    def _mood(self, vid: str, decision: Decision) -> str:
        """How the villager feels about the rumor behind the newcomer line she is answering."""
        uid = decision.source.data["id"]
        player = self._player_behind(decision.source)
        if player is None:
            return ""
        rumor = self._rumor_of(uid)
        return self.rapport.mood(vid, player, rumor, greet=self.rapport.greets(vid, player, rumor))

    def _player_behind(self, m: Match | None) -> str | None:
        """The player whose whisper the item's lineage roots in."""
        return None if m is None else self.lineage.player_of(m.data.get("id"))

    def _about(self, m: Match) -> str | None:
        """The player the item's lineage roots in, or else the player it warns of."""
        uid = m.data.get("id")
        fact = self.facts.get(uid)
        return self.lineage.player_of(uid) or (fact.about if fact else None)

    def _rate(self, vid: str, strength: float, named: list[Match]) -> None:
        """Raise the villager's suspicion of each player the named lines are about by strength."""
        first: dict[str, str | None] = {}
        for m in named:
            if (player := self._about(m)) is not None:
                first.setdefault(player, m.data.get("id"))
        for player, uid in first.items():
            try:
                self.suspicion.rate(vid, player, strength, "heard the newcomer named", uid)
            except Exception:
                log.exception("suspicion rating failed for %s", vid)

    def _exchange(self, m: Match, vid: str, place: str) -> Utterance | None:
        """The line m holds when someone else still here spoke it at this place, so answering it could start a scene."""
        line = self.facts.get(m.data.get("id"))
        if line is None or line.speaker == vid or line.place != place or line.action not in SCENE_SPEECH:
            return None
        if line.speaker not in self.town or self.town.place_of(line.speaker) != place:
            return None
        return line

    async def _enter_scene(self, v: Villager, decision: Decision, place: str) -> tuple[Scene | None, bool]:
        """The villager's scene, the one they join by answering its line, or a new one when their speech answers an
        exchange, and whether they just joined.

        A line that carries a newcomer's rumor starts one anywhere; village talk only where a player is. An answer
        to a full scene's line, or to someone already in another scene, is ordinary talk.
        """
        self._end_stalled_scenes()
        scene = self.scenes.of(v.id)
        if scene is not None or self.rumors is None or decision.action not in SCENE_SPEECH or decision.source is None:
            return scene, False
        held = self.scenes.holding(decision.source.data.get("id"))
        if held is not None:
            if held.place == place and self.scenes.join(held, v.id):
                return held, True
            return None, False
        opening = self._exchange(decision.source, v.id, place)
        if opening is None or self.scenes.of(opening.speaker) is not None:
            return None, False
        root = self.lineage.root(opening.id)
        if self.rumors.carries(root, opening.id):
            scene = self.scenes.start(opening, v.id, root, root)
        elif self.players.at(place):
            scene = self.scenes.start(opening, v.id, opening.id, None)
            if scene is not None:
                await self.rumors.add_topic(opening.id, opening.text, datetime.now(UTC))
        if scene is not None:
            self._show_scene(scene)
        return scene, False

    def _scene_turn(self, scene: Scene, vid: str, decision: Decision, joined: bool) -> None:
        """Apply the villager's turn to a scene that has not ended, and end it or report how it stands."""
        if scene.ended:
            return
        reason = self.scenes.turned(scene, vid, decision.action, decision.style, decision.keen, joined)
        if reason:
            self._end_scene(scene, reason)
        else:
            self._show_scene(scene)

    def _leave_scene(self, vid: str) -> None:
        """Take the villager out of their scene, ending it if that was its last turn."""
        scene = self.scenes.of(vid)
        if scene is None:
            return
        reason = self.scenes.turned(scene, vid, None, None, None, False)
        if reason:
            self._end_scene(scene, reason)

    def _end_stalled_scenes(self) -> None:
        for scene in self.scenes.stalled():
            self._end_scene(scene, "cooled")

    def _end_scene(self, scene: Scene, reason: str) -> None:
        """End the scene and report it; a scene that already ended stays as it ended."""
        if scene.ended:
            return
        self.scenes.end(scene, reason)
        self._show_scene(scene)

    def _show_scene(self, scene: Scene) -> None:
        """Report the scene to its rumor's player, or to the players at its place and those who saw it before."""
        if scene.rumor is not None:
            self.rumors.in_scene(scene.rumor, scene, {vid: self.town.name_of(vid) or vid for vid in scene.participants})
            self.feed.on_scene(scene, player=self.lineage.player_of(scene.rumor))
            if len(scene.people) >= 2 and not scene.ended and self.rumors.first(scene.rumor, f"scene:{scene.id}"):
                kind = "an argument" if scene.heated else "a conversation"
                self.feed.on_moment(scene.rumor, MOMENTS["scene"].format(kind=kind, place=scene.place))
            return
        scene.seen_by.update(p.id for p in self.players.at(scene.place))
        for player_id in sorted(scene.seen_by):
            self.feed.on_scene(scene, player=player_id)

    def share_chance(self, vid: str, source: Match | None) -> float:
        """How likely a villager who walks off is to share source where they arrive: their nature times its pull."""
        pull = 0.5 if source is None or source.clearance is None else source.clearance / self.config.share_clearance
        return self._gossip.get(vid, 0.5) * min(1.0, max(0.0, pull))

    def _fresh(self, m: Match) -> bool:
        """Whether the item clears its need by enough to wake the villager, given how often it was retold.

        The newcomer's own whisper is always fresh.
        """
        if m.clearance is None or self.lineage.is_whisper(m.data.get("id")):
            return True
        fact = self.facts.get(m.data.get("id"))
        depth = fact.depth if fact else 0
        room = self.config.overheard_margin if fact and fact.audience == "room" else 0.0
        return m.clearance >= self.config.fatigue_per_hop * depth + room

    def _names_newcomer(self, m: Match) -> bool:
        """Whether the item names the newcomer; a direct warning already raised suspicion when it was given."""
        # ponytail: Facts evicts after 5000 lines, so very old named lines stop raising suspicion.
        fact = self.facts.get(m.data.get("id"))
        return fact is not None and fact.names_newcomer and fact.action != WARN

    def _report(
        self, v: Villager, whispers: list[str], action: str | None, said: list[tuple[str, int]],
        answered: str | None = None, hops: Sequence[str] = (),
    ) -> None:
        """Tell the feed what the villager did with the whispers this turn heard and any they kept before.

        said holds (utterance id, listeners who heard it) for what this turn said, in order; answered is the
        whisper the villager answered, if any. A reply in an open conversation ends it unless the turn answered.
        hops are the rumor hops told to the villager alone that this turn heard; outside a scene, one not retold was
        kept quiet.
        """
        told: dict[str, int] = {}
        for uid, heard in said:
            told.setdefault(self.lineage.root(uid), heard)
        if self.scenes.of(v.id) is None:
            for root in {self.lineage.root(uid) for uid in hops} - told.keys():
                self.rumors.reached(root, v.id, v.name, "warned" if action == WARN else "kept")
        kept = self._kept.setdefault(v.id, {})
        for wid in [w for w in kept if w in told]:
            del kept[wid]
            self.feed.on_whisper_status(wid, v.id, v.name, "took_the_bait", told=told[wid])
        for wid in whispers:
            if wid == answered:
                self._answered(v, wid)
                continue
            fact = self.facts.get(wid)
            player = self.lineage.player_of(wid)
            talking = fact is not None and fact.conversation is not None and (
                self.conversations.get((v.id, player), {}).get("id") == fact.conversation
            )
            if wid in told:
                self.feed.on_whisper_status(wid, v.id, v.name, "took_the_bait", told=told[wid])
            elif action == WARN:
                self.feed.on_whisper_status(wid, v.id, v.name, "warned")
            else:
                kept[wid] = None
                self.feed.on_whisper_status(wid, v.id, v.name, "moved_on" if talking else "kept")
            if talking and answered is None:
                self.end_conversation(v.id, player)
        while len(kept) > KEPT_WHISPERS:
            del kept[next(iter(kept))]

    def _can_answer(self, vid: str, wid: str) -> bool:
        """Whether the whisper's player has turns left in their conversation with the villager."""
        talking = self.conversations.get((vid, self.lineage.player_of(wid)), {"answered": []})
        return len(talking["answered"]) < self.config.talk_turns

    def _answered(self, v: Villager, wid: str) -> None:
        """Open or extend the whisper's player's conversation on the card the whisper belongs to; the cap ends it."""
        fact = self.facts.get(wid)
        player = self.lineage.player_of(wid)
        conversation = self.conversations.setdefault(
            (v.id, player), {"id": (fact and fact.conversation) or wid, "answered": []},
        )
        conversation["answered"].append(wid)
        self.feed.on_whisper_status(wid, v.id, v.name, "answered")
        if len(conversation["answered"]) >= self.config.talk_turns:
            self.end_conversation(v.id, player)

    async def _clock_loop(self) -> None:
        """Move the village through the parts of each day, announce each one, and walk its daily round."""
        phase_seconds = self.config.day_seconds / len(PHASES)
        while True:
            await asyncio.sleep(phase_seconds)
            self.game.tick()
            self.pressure.decay()
            self.feed.on_time(self.game.day, self.game.phase)
            self._chose_move.clear()
            self._end_stalled_scenes()
            if self.routine is not None:
                if self._walking is not None:
                    self._walking.cancel()
                self._walking = asyncio.create_task(self._walk(self.game.phase, phase_seconds * ROUTINE_SPREAD))

    async def _walk(self, phase: str, spread: float) -> None:
        """Send villagers who are not at their routine place there, a batch at a time across spread seconds."""
        try:
            due = [
                v.id for v in self.town.villagers
                if self.routine.place(v.id, phase) not in (None, self.town.place_of(v.id))
            ]
            batches = [due[i:i + ROUTINE_BATCH] for i in range(0, len(due), ROUTINE_BATCH)]
            for i, batch in enumerate(batches):
                if i:
                    await asyncio.sleep(spread / len(batches))
                free = [vid for vid in batch if self._free_to_walk(vid)]
                await asyncio.gather(*(self._stroll(vid, self.routine.place(vid, phase)) for vid in free))
        except Exception:
            log.exception("routine round failed")

    def _free_to_walk(self, vid: str) -> bool:
        """Whether someone is playing and the villager is in town, free of a turn, a scene, a move of their own, or a
        player talking with them."""
        return self.players.any_playing() and vid in self.town and vid not in self._turning and self.scenes.of(vid) is None and (
            vid not in self._chose_move
        ) and not self._holders(vid)

    def _holders(self, vid: str) -> list[Player]:
        """The players talking with the villager who keep them where they are."""
        return [p for p in self.players if p.holds(vid)]

    async def _stroll(self, vid: str, place: str) -> None:
        """Move the villager to place, perhaps sharing their latest news with someone there once, while it is fresh."""
        if vid not in self.town:
            return
        latest = self._latest.pop(vid, None)
        if latest is not None and not self._fresh(latest):
            latest = None
        v = self.town.get(vid)
        self._strolling.add(vid)
        try:
            with charging(self._player_behind(latest)):
                await act(
                    Decision(MOVE, latest, place, None), v, town=self.town, writer=self.writer,
                    hub=self, lineage=self.lineage, ids=self.ids, facts=self.facts,
                    suspicion_hear_max=self.config.suspicion_hear_max,
                    share_chance=self.share_chance(vid, latest) if latest else 0.0,
                )
            self._report(v, [], MOVE, self._said.pop(vid, []))
        except Exception:
            log.exception("routine move failed for %s", vid)
        finally:
            self._strolling.discard(vid)

    async def _suspicion_loop(self) -> None:
        """Decay suspicion, then check where each player who is still playing stands with the town."""
        days = self.config.suspicion_check_days
        labels: dict[str, str] = {}
        held: dict[str, int] = {}
        while True:
            await asyncio.sleep(days * self.config.day_seconds)
            try:
                self.suspicion.decay(days)
                for p in self.players:
                    if not p.over():
                        self._check(p, labels, held)
            except Exception:
                log.exception("town check failed")

    def _check(self, p: Player, labels: dict[str, str], held: dict[str, int]) -> None:
        """Report the player's label if it changed, and run them out after enough checks at the run-out level."""
        if (now := self.suspicion.label(p.id)) != labels.get(p.id, "quiet"):
            labels[p.id] = now
            self.feed.on_heat(now, player=p.id)
        held[p.id] = held.get(p.id, 0) + 1 if self.suspicion.level(p.id) >= self.config.run_out_level else 0
        if held[p.id] >= self.config.run_out_checks:
            self.feed.say(MEETING, player=p.id)
            p.end(Summary("lost", self.summary_source(p), self.suspicion.reasons(p.id)))

    async def _renew_loop(self) -> None:
        """Renew the lease of every mind not read within renew_seconds."""
        while True:
            await asyncio.sleep(self.config.renew_seconds)
            cutoff = time.monotonic() - self.config.renew_seconds
            for vid, mind in list(self.minds.items()):
                if self._read_at.get(vid, 0.0) > cutoff:
                    continue
                try:
                    await mind.renew()
                    self._read_at[vid] = time.monotonic()
                except Exception:
                    log.exception("renew failed for %s", vid)


async def _cancel(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
