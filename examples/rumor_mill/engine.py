"""The engine: one worker per villager, woken only by Contex pushes."""

import asyncio
import logging
import random
import time
from collections.abc import Callable
from contextlib import aclosing, suppress
from datetime import UTC, datetime

from examples.rumor_mill.actions import ANSWER, MOVE, WARN
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.decide import Decision, TurnView, decide
from examples.rumor_mill.effects import act
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.game import PHASES, Game, Summary
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import Mind, group_project, said_project
from examples.rumor_mill.models import Group, Utterance, Villager
from examples.rumor_mill.overhear import Overhear
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.scenes import SCENE_SPEECH, Scene, Scenes
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import START_PLACE, Town
from examples.rumor_mill.writer import WHISPER_FRAMING, Writer

log = logging.getLogger(__name__)

LISTEN_RETRY_SECONDS = 1
# How many keys that reached a turn are remembered per villager, so neither a poll nor a direct hit hears them again.
CONSUMED_KEYS = 500
# How many whispers a villager kept to themselves are watched for a later relay.
KEPT_WHISPERS = 50
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
        summary_source: Callable[[], list[tuple[str, int]]] = list,
        baselines: Baselines | None = None,
        rumors: Rumors | None = None,
        overhear: Overhear | None = None,
    ):
        self.config = config
        self.town = town
        self.contex = contex
        self.jev = jev
        self.writer = writer
        self.lineage = lineage
        self.ids = ids
        self.feed = feed
        self.facts = Facts()
        self.suspicion = Suspicion(config, town)
        self.scenes = Scenes(config, town, random.Random())
        self.baselines = baselines
        self.rumors = rumors
        self.overhear = overhear
        self.player_place: Callable[[], str] = lambda: START_PLACE
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
        self._consumed: dict[str, dict[str, None]] = {}
        self._listen_failed: set[str] = set()
        self._said: dict[str, list[tuple[str, int]]] = {}
        self._kept: dict[str, dict[str, None]] = {}
        # How readily each villager passes news on, asked once per villager.
        self._gossip: dict[str, float] = {}
        # Each villager's open conversation with the newcomer: its first whisper and the whispers answered so far.
        self.conversations: dict[str, dict] = {}
        self._renewal: asyncio.Task | None = None
        self._checks: asyncio.Task | None = None
        self._clock: asyncio.Task | None = None
        self._turns = asyncio.Semaphore(config.turn_concurrency)

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
        tasks += [t for t in (self._renewal, self._checks, self._clock) if t]
        self._tasks.clear()
        self._renewal = self._checks = self._clock = None
        if self.rumors is not None:
            self.rumors.close()
        await _cancel(tasks)

    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None:
        """Publish the utterance to each listener's mind; raises only if every listener failed.

        An addressed utterance also wakes each listener and joins their next turn unmatched. A line that goes on
        with a scene is heard by those it addresses too, and wakes them while it stays on the scene's topic.
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
        if heard and on_topic and utterance.speaker in self._turning:
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
        if self.overhear is not None:
            hop = on_topic and self.lineage.is_player_rooted(utterance.id)
            try:
                reason = await self.overhear.keep(utterance, self.player_place(), hop)
            except Exception:
                log.warning("overhearing %s failed", utterance.id)
                log.debug("overhearing failure", exc_info=True)
            else:
                if reason:
                    self.feed.on_overheard(utterance, reason)

    async def reply(self, utterance: Utterance) -> None:
        """Remember a villager's answer to the newcomer and show it on its conversation; no mind hears it."""
        await self._remember_said(utterance.speaker, f"said:{utterance.id}", utterance.to_data())
        whisper = self.facts.get(utterance.parent)
        conversation = (whisper and whisper.conversation) or utterance.parent
        if self.rumors is not None:
            self.rumors.add_line(conversation, utterance)
        self.feed.on_reply(conversation, utterance)

    def end_conversation(self, vid: str) -> None:
        """Close the villager's conversation with the newcomer; every answered whisper in it has moved on."""
        conversation = self.conversations.pop(vid, None)
        if conversation is not None:
            name = self.town.name_of(vid) or vid
            for wid in conversation["answered"]:
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

    async def _carries_rumor(self, utterance: Utterance, heard: list[str]) -> bool:
        """Whether a retelling of a whisper still carries it, recorded on its card; True for any other line."""
        root = self.lineage.root(utterance.id)
        if not heard or self.rumors is None or root == utterance.id or not self.lineage.is_whisper(root):
            return True
        on_topic = await self.rumors.on_topic(root, utterance.text)
        if self.rumors.record(root, utterance, {i: self.town.name_of(i) or i for i in heard}, on_topic):
            self.feed.on_drift(root)
        return on_topic

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

    def suspect(self, vid: str, amount: float, reason: str, uid: str | None = None) -> None:
        self.suspicion.bump(vid, amount, reason, uid)

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

        Returns whether a turn ran.
        """
        if self.game is not None and self.game.finished.is_set():
            return False
        v = mind.villager
        new = await mind.poll()
        self._read_at[v.id] = time.monotonic()
        consumed = self._consumed.setdefault(v.id, {})
        pending = self._pending.pop(v.id, [])
        new = [m for m in new if m.data_key not in consumed]
        new += [m for m in pending if m.data_key not in consumed and m.data_key not in mind.seen]
        for m in new:
            consumed[m.data_key] = None
        while len(consumed) > CONSUMED_KEYS:
            del consumed[next(iter(consumed))]
        stale = [m for m in new if not self._fresh(m)]
        stale_keys = {m.data_key for m in stale}
        new = [m for m in new if m.data_key not in stale_keys]
        if not new:
            return False
        keys = {m.data_key for m in new}
        # The newcomer's own whispers among the new items, for whisper outcomes.
        whispers = [m for m in new if self.lineage.is_whisper(m.data.get("id"))]
        said = await self._recall_said(v.id, whispers) if whispers else []
        talking = self.conversations.get(v.id, {"answered": []})
        answerable = whispers if len(talking["answered"]) < self.config.talk_turns else []
        place = self.town.place_of(v.id)
        view = TurnView(
            villager=v,
            place=place,
            present=self.town.present(place, exclude=v.id),
            member_groups=self.town.member_groups(v.id),
            visible_groups=self.town.visible_groups(v.id),
            new=new,
            recent=stale + [m for m in mind.matches if m.data_key not in keys | stale_keys],
            suspicion=self.suspicion.score(v.id),
            warn_threshold=self.config.warn_threshold,
            names_newcomer=[m for m in new if m in whispers or self._names_newcomer(m)],
            said=said,
            answerable=answerable,
            ask_gossip=v.id not in self._gossip,
            ask_keen=self.scenes.of(v.id) is not None or any(self._exchange(m, v.id, place) for m in new),
        )
        action = answered = None
        try:
            async with self._turns:
                decision = await decide(self.jev, view)
                action = decision.action
                if self.game is not None and self.game.finished.is_set():
                    self._pending.pop(v.id, None)
                    return True
                if decision.suspicion_strength is not None:
                    try:
                        self.suspicion.rate(
                            v.id, decision.suspicion_strength, "heard the newcomer named",
                            view.names_newcomer[0].data.get("id"),
                        )
                    except Exception:
                        log.exception("suspicion rating failed for %s", v.id)
                if decision.gossip is not None:
                    self._gossip[v.id] = decision.gossip
                scene, joined = await self._enter_scene(v, decision, place)
                await act(
                    decision, v, town=self.town, writer=self.writer, hub=self,
                    lineage=self.lineage, ids=self.ids, facts=self.facts,
                    inspiration=[m.data.get("text", "") for m in new],
                    said=[m.data.get("text", "") for m in said],
                    suspicion_hear_max=self.config.suspicion_hear_max,
                    share_chance=self.share_chance(v.id, decision.source) if action == MOVE else 0.0,
                )
                if scene is not None:
                    self._scene_turn(scene, v.id, decision, joined)
                if action == ANSWER:
                    answered = decision.source.data["id"]
        finally:
            self._report(v, [m.data["id"] for m in whispers], action, self._said.pop(v.id, []), answered)
        return True

    def _exchange(self, m: Match, vid: str, place: str) -> Utterance | None:
        """The line m holds when someone else still here spoke it at this place, so answering it could start a scene."""
        line = self.facts.get(m.data.get("id"))
        if line is None or line.speaker == vid or line.place != place or line.action not in SCENE_SPEECH:
            return None
        if line.speaker not in self.town or self.town.place_of(line.speaker) != place:
            return None
        return line

    async def _enter_scene(self, v: Villager, decision: Decision, place: str) -> tuple[Scene | None, bool]:
        """The villager's scene, or a new one when their speech answers an exchange, and whether they just joined.

        A line that carries the newcomer's rumor starts one anywhere; village talk only where the player is.
        """
        scene = self.scenes.of(v.id)
        if scene is not None or self.rumors is None or decision.action not in SCENE_SPEECH or decision.source is None:
            return scene, False
        opening = self._exchange(decision.source, v.id, place)
        if opening is None:
            return None, False
        root = self.lineage.root(opening.id)
        if self.rumors.carries(root, opening.id):
            scene = self.scenes.start(opening, v.id, root, root)
        elif place == self.player_place() and not self.scenes.full():
            await self.rumors.add_topic(opening.id, opening.text, datetime.now(UTC))
            scene = self.scenes.start(opening, v.id, opening.id, None)
        if scene is not None:
            self.feed.on_scene(scene)
        return scene, False

    def _scene_turn(self, scene: Scene, vid: str, decision: Decision, joined: bool) -> None:
        """Apply the villager's turn to a scene that has not ended, and end it or report how it stands."""
        if scene.ended:
            return
        reason = self.scenes.turned(scene, vid, decision.action, decision.style, decision.keen, joined)
        if reason:
            self._end_scene(scene, reason)
        else:
            self.feed.on_scene(scene)

    def _leave_scene(self, vid: str) -> None:
        """Take the villager out of their scene, ending it if that was its last turn."""
        scene = self.scenes.of(vid)
        if scene is None:
            return
        reason = self.scenes.turned(scene, vid, None, None, None, False)
        if reason:
            self._end_scene(scene, reason)

    def _end_scene(self, scene: Scene, reason: str) -> None:
        """End the scene and report it; a scene that already ended stays as it ended."""
        if scene.ended:
            return
        self.scenes.end(scene, reason)
        self.feed.on_scene(scene)

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
        answered: str | None = None,
    ) -> None:
        """Tell the feed what the villager did with the whispers this turn heard and any they kept before.

        said holds (utterance id, listeners who heard it) for what this turn said, in order; answered is the
        whisper the villager answered, if any. A reply in an open conversation ends it unless the turn answered.
        """
        told: dict[str, int] = {}
        for uid, heard in said:
            told.setdefault(self.lineage.root(uid), heard)
        kept = self._kept.setdefault(v.id, {})
        for wid in [w for w in kept if w in told]:
            del kept[wid]
            self.feed.on_whisper_status(wid, v.id, v.name, "took_the_bait", told=told[wid])
        for wid in whispers:
            if wid == answered:
                self._answered(v, wid)
                continue
            fact = self.facts.get(wid)
            talking = fact is not None and fact.conversation is not None and (
                self.conversations.get(v.id, {}).get("id") == fact.conversation
            )
            if wid in told:
                self.feed.on_whisper_status(wid, v.id, v.name, "took_the_bait", told=told[wid])
            elif action == WARN:
                self.feed.on_whisper_status(wid, v.id, v.name, "warned")
            else:
                kept[wid] = None
                self.feed.on_whisper_status(wid, v.id, v.name, "moved_on" if talking else "kept")
            if talking and answered is None:
                self.end_conversation(v.id)
        while len(kept) > KEPT_WHISPERS:
            del kept[next(iter(kept))]

    def _answered(self, v: Villager, wid: str) -> None:
        """Open or extend the villager's conversation on the card the whisper belongs to; the cap ends it."""
        fact = self.facts.get(wid)
        conversation = self.conversations.setdefault(v.id, {"id": (fact and fact.conversation) or wid, "answered": []})
        conversation["answered"].append(wid)
        self.feed.on_whisper_status(wid, v.id, v.name, "answered")
        if len(conversation["answered"]) >= self.config.talk_turns:
            self.end_conversation(v.id)

    async def _clock_loop(self) -> None:
        """Move the village through the parts of each day and announce each one."""
        while not self.game.finished.is_set():
            await asyncio.sleep(self.config.day_seconds / len(PHASES))
            if self.game.finished.is_set():
                return
            self.game.tick()
            self.feed.on_time(self.game.day, self.game.phase)

    async def _suspicion_loop(self) -> None:
        """Decay suspicion, report label changes, and lose once the town holds at the run-out level."""
        days = self.config.suspicion_check_days
        label, held = "quiet", 0
        while not self.game.finished.is_set():
            await asyncio.sleep(days * self.config.day_seconds)
            if self.game.finished.is_set():
                return
            try:
                self.suspicion.decay(days)
                if (now := self.suspicion.label()) != label:
                    label = now
                    self.feed.on_heat(label)
                held = held + 1 if self.suspicion.level() >= self.config.run_out_level else 0
                if held >= self.config.run_out_checks:
                    summary = Summary("lost", self.summary_source(), self.suspicion.reasons())
                    self.feed.say(MEETING)
                    self.game.lose(summary)
            except Exception:
                log.exception("town check failed")

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
