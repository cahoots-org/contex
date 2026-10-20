"""The engine: one worker per villager, woken only by Contex pushes."""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import aclosing, suppress

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient, Match
from examples.rumor_mill.decide import TurnView, decide
from examples.rumor_mill.effects import act
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import Mind, group_project
from examples.rumor_mill.models import Group, Utterance, Villager
from examples.rumor_mill.suspicion import Suspicion
from examples.rumor_mill.town import START_PLACE, Town
from examples.rumor_mill.writer import Writer

log = logging.getLogger(__name__)

LISTEN_RETRY_SECONDS = 1


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
    ):
        self.config = config
        self.town = town
        self.contex = contex
        self.jev = jev
        self.writer = writer
        self.lineage = lineage
        self.ids = ids
        self.feed = feed
        self.suspicion = Suspicion(config, town)
        self.mind_factory = mind_factory or (lambda v: Mind(contex, v, config))
        self.on_heard: Callable[[str, list[Match]], None] | None = None
        self.minds: dict[str, Mind] = {}
        self.departures: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._read_at: dict[str, float] = {}
        self._wakes: dict[str, asyncio.Event] = {}
        self._pending: dict[str, list[Match]] = {}
        self._listen_failed: set[str] = set()
        self._renewal: asyncio.Task | None = None
        self._turns = asyncio.Semaphore(config.turn_concurrency)
        self._started = time.monotonic()

    @property
    def day(self) -> int:
        return int((time.monotonic() - self._started) / self.config.day_seconds)

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
        self._listen_failed.discard(vid)
        with suppress(KeyError):
            self.town.remove(vid)
        current = asyncio.current_task()
        tasks = [t for t in self._tasks.pop(vid, []) if t is not current]
        await _cancel(tasks)

    async def run(self) -> None:
        """Start the clock and lease renewal; returns at once."""
        self._started = time.monotonic()
        self._renewal = asyncio.create_task(self._renew_loop())

    async def stop(self) -> None:
        tasks = [t for ts in self._tasks.values() for t in ts]
        if self._renewal:
            tasks.append(self._renewal)
        self._tasks.clear()
        self._renewal = None
        await _cancel(tasks)

    async def deliver(self, utterance: Utterance, listener_ids: list[str], addressed: bool = False) -> None:
        """Publish the utterance to each listener's mind; raises only if every listener failed.

        An addressed utterance also wakes each listener and joins their next turn unmatched.
        """
        listeners = [i for i in listener_ids if i in self.town and i in self.minds]
        data = utterance.to_data(self.day)
        results = await asyncio.gather(
            *(self.minds[i].hear(f"utt:{utterance.id}", data) for i in listeners), return_exceptions=True,
        )
        for vid, result in zip(listeners, results):
            if isinstance(result, Exception):
                log.error("delivery to %s failed", vid, exc_info=result)
        heard = [vid for vid, result in zip(listeners, results) if not isinstance(result, Exception)]
        if addressed:
            for vid in heard:
                wake = self._wakes.get(vid)
                if wake is None:
                    continue
                self._pending.setdefault(vid, []).append(Match(f"utt:{utterance.id}", data))
                wake.set()
        if listeners and not heard:
            raise next(r for r in results if isinstance(r, Exception))
        self.feed.on_delivery(utterance, heard)

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
        last_turn = float("-inf")
        while True:
            await wake.wait()
            await asyncio.sleep(max(0.0, last_turn + self.config.cooldown_seconds - time.monotonic()))
            wake.clear()
            try:
                if await self._turn(mind):
                    last_turn = time.monotonic()
            except Exception:
                log.exception("turn failed for %s", vid)
                last_turn = time.monotonic()
                if self._pending.get(vid):
                    wake.set()
            if vid not in self.town:
                await self.remove_villager(vid)
                return

    async def _turn(self, mind: Mind) -> bool:
        """Poll the mind, add addressed messages, and if anything is new, decide and act.

        Returns whether a turn ran.
        """
        v = mind.villager
        new = await mind.poll()
        self._read_at[v.id] = time.monotonic()
        polled = {m.data_key for m in new}
        new = new + [m for m in self._pending.pop(v.id, []) if m.data_key not in polled]
        if not new:
            return False
        if self.on_heard:
            self.on_heard(v.id, new)
        keys = {m.data_key for m in new}
        place = self.town.place_of(v.id)
        view = TurnView(
            villager=v,
            place=place,
            present=self.town.present(place, exclude=v.id),
            member_groups=self.town.member_groups(v.id),
            visible_groups=self.town.visible_groups(v.id),
            new=new,
            recent=[m for m in mind.matches if m.data_key not in keys],
            suspicion=self.suspicion.score(v.id),
            warn_threshold=self.config.warn_threshold,
            player_rooted=[m for m in new if self.lineage.is_player_rooted(m.data.get("id"))],
        )
        async with self._turns:
            decision = await decide(self.jev, view)
            if decision.suspicion_strength is not None:
                try:
                    self.suspicion.rate(
                        v.id, decision.suspicion_strength, "heard the newcomer named", view.player_rooted[0].data.get("id"),
                    )
                except Exception:
                    log.exception("suspicion rating failed for %s", v.id)
            await act(
                decision, v, town=self.town, writer=self.writer, hub=self,
                lineage=self.lineage, ids=self.ids, day=self.day,
                inspiration=[m.data.get("text", "") for m in new],
                suspicion_hear_max=self.config.suspicion_hear_max,
            )
        return True

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
