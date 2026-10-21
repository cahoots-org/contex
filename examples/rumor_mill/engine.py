"""The engine: one worker per villager, woken only by Contex pushes."""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import aclosing, suppress

from examples.rumor_mill.actions import WARN
from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.decide import TurnView, decide
from examples.rumor_mill.effects import act
from examples.rumor_mill.facts import Facts
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import Mind, group_project
from examples.rumor_mill.models import Group, Utterance, Villager
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import START_PLACE, Town
from examples.rumor_mill.writer import Writer

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
        self.baselines = baselines
        self.rumors = rumors
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
        self._renewal: asyncio.Task | None = None
        self._checks: asyncio.Task | None = None
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

    async def stop(self) -> None:
        tasks = [t for ts in self._tasks.values() for t in ts]
        tasks += [t for t in (self._renewal, self._checks) if t]
        self._tasks.clear()
        self._renewal = self._checks = None
        if self.rumors is not None:
            self.rumors.close()
        await _cancel(tasks)

    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None:
        """Publish the utterance to each listener's mind; raises only if every listener failed.

        An addressed utterance also wakes each listener and joins their next turn unmatched.
        """
        listeners = [i for i in listener_ids if i in self.town and i in self.minds]
        data = utterance.to_data()
        results = await asyncio.gather(
            *(self.minds[i].hear(f"utt:{utterance.id}", data) for i in listeners), return_exceptions=True,
        )
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
        self.feed.on_delivery(utterance, heard, on_topic)

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
        """Wall-clock time when the villager can next take a turn; now or earlier when free."""
        if vid in self._turning:
            return time.time() + self.config.cooldown_seconds
        last = self._last_turn.get(vid)
        if last is None:
            return time.time()
        return time.time() + last + self.config.cooldown_seconds - time.monotonic()

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
        )
        action = None
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
                await act(
                    decision, v, town=self.town, writer=self.writer, hub=self,
                    lineage=self.lineage, ids=self.ids, facts=self.facts,
                    inspiration=[m.data.get("text", "") for m in new],
                    suspicion_hear_max=self.config.suspicion_hear_max,
                )
        finally:
            self._report(v, [m.data["id"] for m in whispers], action, self._said.pop(v.id, []))
        return True

    def _fresh(self, m: Match) -> bool:
        """Whether the item clears its need by enough to wake the villager, given how often it was retold."""
        if m.clearance is None:
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

    def _report(self, v: Villager, whispers: list[str], action: str | None, said: list[tuple[str, int]]) -> None:
        """Tell the feed what the villager did with the whispers this turn heard and any they kept before.

        said holds (utterance id, listeners who heard it) for what this turn said, in order.
        """
        told: dict[str, int] = {}
        for uid, heard in said:
            told.setdefault(self.lineage.root(uid), heard)
        kept = self._kept.setdefault(v.id, {})
        for wid in [w for w in kept if w in told]:
            del kept[wid]
            self.feed.on_whisper_status(wid, v.id, v.name, "took_the_bait", told=told[wid])
        for wid in whispers:
            if wid in told:
                self.feed.on_whisper_status(wid, v.id, v.name, "took_the_bait", told=told[wid])
            elif action == WARN:
                self.feed.on_whisper_status(wid, v.id, v.name, "warned")
            else:
                kept[wid] = None
                self.feed.on_whisper_status(wid, v.id, v.name, "kept")
        while len(kept) > KEPT_WHISPERS:
            del kept[next(iter(kept))]

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
