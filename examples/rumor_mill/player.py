"""The player's whispers: validation, delivery, and whether each one landed."""

import asyncio
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import mind_project
from examples.rumor_mill.overhear import audible
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.town import NEWCOMER, PLACES, START_PLACE, Town
from examples.rumor_mill.usage import charging

log = logging.getLogger(__name__)

MAX_WHISPER_CHARS = 500
ECHO_WORDS = 4
GAME_OVER = "The game is over."
NOT_CARRIED = "The whisper didn't carry; try again."
THREAD_WHISPERS = 2
LISTEN_OPENING = 5
LISTEN_LOOKBACK_DAYS = 1 / 4
ALREADY_LISTENING = "You're already listening."


class Refusal(str):
    """The message for a whisper a suspicious villager turned away; the feed has already shown it."""


def _runs(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(words[i:i + ECHO_WORDS]) for i in range(len(words) - ECHO_WORDS + 1)}


def echoes(line: str, concern: str) -> bool:
    """Whether the line repeats four or more consecutive words of the concern."""
    return bool(_runs(line) & _runs(concern))


class Player:
    def __init__(
        self, engine, town: Town, lineage: Lineage, ids: Ids, feed, config: Config, writer=None, game: Game | None = None,
        *, player_id: str, owner: str | None = None,
    ):
        self.id = player_id
        # The person behind this run; they keep their trophies across runs.
        self.owner = player_id if owner is None else owner
        self.engine = engine
        self.town = town
        self.lineage = lineage
        self.ids = ids
        self.feed = feed
        self.config = config
        self.writer = writer
        self.game = game
        # Where the player last whispered.
        self.place = START_PLACE
        self.state = "playing"
        self.summary: Summary | None = None
        self.finished = asyncio.Event()
        self.ended_at: float | None = None
        self.whispered_at: float | None = None
        self.goals: list[Goal] = []
        # The ids of this hand's goals met so far.
        self.met: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        # When the running listen ends, or None when the player is not listening.
        self._listen_until: float | None = None
        self._hinted: dict[str, set[int]] = {}
        self._threads: dict[str, dict] = {}

    async def whisper(self, name: str, text: str) -> str | None:
        """Whisper text to the villager called name; returns why it could not be done, if it couldn't.

        A villager turning the whisper away comes back as a Refusal.
        """
        if self.over():
            return GAME_OVER
        name = name.strip()
        if not name or not text.strip():
            return "Whisper to whom, and what? Try: Marta: the miller waters his flour"
        found = self.find(name)
        if isinstance(found, str):
            return found
        return await self.whisper_to(found.id, text)

    def find(self, name: str) -> Villager | str:
        """The one villager called name, or the message saying why there isn't one."""
        matches = self.town.find(name)
        if not matches:
            gone = [v for v in self.town.departed if v.name.lower() == name.lower()]
            return f"{gone[0].name} has left town." if gone else f"No one in town is called {name}."
        if len(matches) > 1:
            return f"Which {name}? " + ", ".join(v.name for v in matches)
        return matches[0]

    def walk(self, place: str) -> str | None:
        """Move to place for free; returns why it could not be done, if it couldn't."""
        if self.over():
            return GAME_OVER
        if place not in PLACES:
            return f"There's no {place} here. Try one of: {', '.join(PLACES)}."
        if place == self.place:
            return f"You're already at the {place}."
        self._move(place)
        return None

    def listen(self) -> str | None:
        """Listen to everything said where the player stands for a while; returns why it could not be done, if it couldn't.

        It opens with the last few lines said here in this part of the day.
        """
        if self.over():
            return GAME_OVER
        if self.listening():
            return ALREADY_LISTENING
        now = time.time()
        until = self._listen_until = now + self.config.listen_seconds
        since = now - self.config.day_seconds * LISTEN_LOOKBACK_DAYS
        heard = [
            u for u in self.engine.facts.recent(self.place, since)
            if audible(u) and self.lineage.player_of(u.id) != self.id
        ]
        self.feed.on_listen(self.place, heard[-LISTEN_OPENING:], until, player=self.id)
        self._track(asyncio.create_task(self._end_listen(until)))
        return None

    def listening(self) -> bool:
        """Whether the player is listening now."""
        return self._listen_until is not None

    async def _end_listen(self, until: float) -> None:
        await asyncio.sleep(self.config.listen_seconds)
        if self._listen_until == until:
            self._stop_listening()

    def _stop_listening(self) -> None:
        if self._listen_until is None:
            return
        self._listen_until = None
        self.feed.on_listen_end(self.place, player=self.id)

    def _move(self, place: str) -> None:
        self._stop_listening()
        self.place = place
        self.feed.on_place(place, player=self.id)

    async def whisper_to(self, villager_id: str, text: str) -> str | None:
        """Whisper text to the villager with this id; returns why it could not be done, if it couldn't.

        A villager turning the whisper away comes back as a Refusal. Every model call it sets off is charged to this player.
        """
        with charging(self.id):
            return await self._whisper_to(villager_id, text)

    async def _whisper_to(self, villager_id: str, text: str) -> str | None:
        if self.over():
            return GAME_OVER
        text = text.strip()
        if villager_id not in self.town:
            gone = self.town.name_of(villager_id)
            return f"{gone} has left town." if gone else "No one in town has that id."
        if not text:
            return "Whisper what? Try: the miller waters his flour"
        if len(text) > MAX_WHISPER_CHARS:
            return f"Too long: a whisper is at most {MAX_WHISPER_CHARS} characters."
        target = self.town.get(villager_id)
        if self.engine.suspicion.score(target.id, self.id) >= self.config.refuse_threshold:
            self.engine.suspicion.bump(target.id, self.id, self.config.suspicion_whisper_bump, "tried to whisper again")
            self.engine.end_conversation(target.id, self.id)
            self.feed.on_refusal(target.id, target.name, await self._turn_away(target), player=self.id)
            return Refusal(f"{target.name} turns away from you.")
        uid = self.ids.next("w")
        self.lineage.record(uid, None, player=self.id)
        conversation = self.engine.conversations.get((target.id, self.id))
        thread = self._thread(target.id)
        hint_need = None
        if conversation:
            thread_id = conversation["id"]
        elif thread:
            thread_id, hint_need = thread["id"], thread["need"]
        else:
            thread_id = None
        utterance = Utterance(
            uid, NEWCOMER, "A newcomer", self.town.place_of(target.id), text, None, "whispered to you",
            conversation=thread_id,
        )
        self.engine.facts.record(utterance)
        published_at = datetime.now(UTC)
        since = (published_at - timedelta(seconds=self.config.check_lookback_seconds)).isoformat()
        try:
            await self.engine.deliver(utterance, [target.id])
        except Exception:
            log.exception("whisper to %s failed", target.id)
            return NOT_CARRIED
        self.whispered_at = time.time()
        if self.game is not None:
            self.game.moved(self.id)
        if utterance.place != self.place:
            self._move(utterance.place)
        self.engine.suspicion.bump(
            target.id, self.id, self.config.suspicion_whisper_bump, "whispered to them directly", uid,
        )
        self.feed.on_whisper_status(uid, target.id, target.name, "checking")
        found, _ = await asyncio.gather(self.check(target, utterance, since, hint_need), self._card(utterance, target, published_at))
        if found is None:
            self.feed.on_whisper_status(uid, target.id, target.name, "not_carried")
            return NOT_CARRIED
        hits = [m for _, m in found if m.clearance >= 0]
        near_misses = [(need, m) for need, m in found if m.clearance < 0]
        if hits:
            hit = Match(
                f"utt:{uid}", utterance.to_data(),
                max(h.similarity for h in hits), clearance=max(h.clearance for h in hits),
            )
        else:
            # A story the villager already heard lands whatever their needs.
            familiar = await self.engine.recall_heard(target.id, text, uid)
            hit = Match(f"utt:{uid}", utterance.to_data(), familiar[0].similarity, clearance=0.0) if familiar else None
        if hit:
            self._threads.pop(target.id, None)
            if not self.engine.hear_directly(target.id, hit):
                return None
            until = self.engine.cooldown_ends(target.id)
            if until > time.time():
                self.feed.on_whisper_status(uid, target.id, target.name, "busy", until=until)
            else:
                self.feed.on_whisper_status(uid, target.id, target.name, "heard")
        else:
            leaning = bool(near_misses) and not (thread and thread["whispers"] + 1 >= THREAD_WHISPERS)
            state = "near_miss" if leaning else "brushed_off"
            self.feed.on_whisper_status(uid, target.id, target.name, state)
            open_now = self.engine.conversations.get((target.id, self.id))
            if utterance.conversation and open_now and open_now["id"] == utterance.conversation:
                self.engine.end_conversation(target.id, self.id)
            if leaning:
                concern = max(near_misses, key=lambda n: n[1].clearance)[0]
            else:
                concern = self._next_concern(target.id)
            self._extend_thread(target.id, uid, concern)
            task = asyncio.create_task(self._hint(target.id, target.name, uid, concern, state, text))
            self._track(task)
        return None

    def _now(self) -> tuple[int, str] | None:
        return (self.game.day, self.game.phase) if self.game is not None else None

    def _thread(self, villager_id: str) -> dict | None:
        """The villager's open thread, dropped once the day has moved on to its next part."""
        thread = self._threads.get(villager_id)
        if thread and thread["at"] != self._now():
            del self._threads[villager_id]
            return None
        return thread

    def _extend_thread(self, villager_id: str, uid: str, concern: str | None) -> None:
        """Open a thread on a missed whisper, or count one more on it, closing it at THREAD_WHISPERS."""
        thread = self._thread(villager_id)
        if thread is None:
            self._threads[villager_id] = {"id": uid, "need": concern, "whispers": 1, "at": self._now()}
            return
        thread["whispers"] += 1
        thread["need"] = concern
        if thread["whispers"] >= THREAD_WHISPERS:
            del self._threads[villager_id]

    async def check(
        self, target: Villager, utterance: Utterance, since: str, hint_need: str | None = None,
    ) -> list[tuple[str, Match]] | None:
        """The whisper's (need, match) pairs within the near-miss band of each need's bar, None on error.

        Each match carries its clearance against the bar: below zero is a near miss.
        The hinted need is held to its bare baseline plus the hint margin; the rest to their full threshold.
        """
        key = f"utt:{utterance.id}"
        project = mind_project(self.config.session, target.id)
        bars = {need: self._bar(need, need == hint_need) for need in target.needs}
        floor = self.config.near_miss_band
        try:
            found = await asyncio.gather(*(
                self.engine.contex.query(project, need, top_k=self.config.top_k, since=since, threshold=bars[need] - floor)
                for need in target.needs
            ))
        except Exception:
            log.exception("whisper check for %s failed", target.id)
            return None
        return [
            (need, replace(m, clearance=m.similarity - bars[need]))
            for need, matches in zip(target.needs, found) for m in matches
            if m.data_key == key and m.similarity is not None and m.similarity >= bars[need] - floor
        ]

    def _bar(self, need: str, hinted: bool) -> float:
        threshold = self.engine.baselines.threshold(need)
        if not hinted:
            return threshold
        return threshold - self.config.match_margin + self.config.hint_margin

    async def _card(self, utterance: Utterance, target: Villager, published_at: datetime) -> None:
        if self.engine.rumors is not None:
            await self.engine.rumors.add(
                utterance.id, utterance.text, published_at, target.id, target.name, utterance.conversation,
                player=self.id,
            )

    def over(self) -> bool:
        """Whether this player's game is over."""
        return self.finished.is_set()

    def end(self, summary: Summary) -> None:
        """End this player's game once; announce it to them, then send them nothing more."""
        if self.over():
            return
        self.state, self.summary, self.ended_at = summary.outcome, summary, time.time()
        self.finished.set()
        self.feed.on_game_over(summary, player=self.id)
        self.feed.close(self.id)

    def close(self) -> None:
        """Cancel every background task this player started."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _hint(
        self, villager_id: str, name: str, uid: str, concern: str | None, state: str, text: str,
    ) -> None:
        """Add the brush-off or lean-in line, or nothing, to a missed whisper's status."""
        if self.over():
            return
        try:
            if state == "near_miss":
                line = await self._go_on(villager_id, text)
            else:
                line = await self._brush_off(villager_id, concern)
        except Exception:
            log.exception("%s line for %s failed", state, villager_id)
            line = None
        if self.over():
            return
        self.feed.on_whisper_status(uid, villager_id, name, state, hint=line)
        if line:
            await self.engine.hints.add(self.id, villager_id, line)

    def _next_concern(self, villager_id: str) -> str | None:
        """One of the villager's needs not hinted at yet, starting over once all have been."""
        needs = self.town.get(villager_id).needs
        if not needs:
            return None
        used = self._hinted.setdefault(villager_id, set())
        if len(used) >= len(needs):
            used.clear()
        i = next(i for i in range(len(needs)) if i not in used)
        used.add(i)
        return needs[i]

    async def _turn_away(self, villager: Villager) -> str | None:
        """An in-character refusal line, or None when there is no writer or it fails."""
        if self.writer is None:
            return None
        try:
            return await self.writer.turn_away(villager) or None
        except Exception:
            log.exception("turn-away for %s failed", villager.id)
            return None

    async def _brush_off(self, villager_id: str, concern: str | None) -> str | None:
        """An in-character line hinting at a concern, or None for a plain shrug."""
        if self.writer is None or concern is None or villager_id not in self.town:
            return None
        for _ in range(2):
            try:
                line = await self.writer.brush_off(self.town.get(villager_id), concern)
            except Exception:
                log.exception("brush-off for %s failed", villager_id)
                return None
            if line and not echoes(line, concern):
                return line
        return None

    async def _go_on(self, villager_id: str, text: str) -> str | None:
        """An in-character line urging the newcomer on, or None for a plain lean-in."""
        if self.writer is None or villager_id not in self.town:
            return None
        try:
            return await self.writer.go_on(self.town.get(villager_id), text) or None
        except Exception:
            log.exception("go-on for %s failed", villager_id)
            return None
