"""The player's whispers: validation, delivery, and whether each one landed."""

import asyncio
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from typesafe_sdk import Noul

from examples.whispers.config import Config
from examples.whispers.contex_client import Match
from examples.whispers.game import Game, Summary
from examples.whispers.goals import Goal
from examples.whispers.lineage import Ids, Lineage
from examples.whispers.mind import mind_project
from examples.whispers.overhear import audible
from examples.whispers.models import Utterance, Villager
from examples.whispers.town import NEWCOMER, PLACES, START_PLACE, Town, neighbours
from examples.whispers.usage import charging

log = logging.getLogger(__name__)

MAX_WHISPER_CHARS = 500
ECHO_WORDS = 4
GAME_OVER = "The game is over."
NOT_CARRIED = "The whisper didn't carry; try again."
LISTEN_OPENING = 5
LISTEN_LOOKBACK_DAYS = 1 / 4
ALREADY_LISTENING = "You're already listening."
ASK_LINES = 3


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
        # Per villager, the last whisper that landed with them and its rumor; it holds while they have not acted on it.
        self._held: dict[str, tuple[str, str]] = {}
        # Per villager, when the player last whispered to them.
        self._said_at: dict[str, float] = {}
        # When the player last typed in the whisper box.
        self._typed_at = 0.0

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
        thread = self._thread(target.id)
        hint_need = None
        if thread and (target.id, self.id) not in self.engine.conversations:
            hint_need = thread["need"]
        rumors = self.engine.rumors
        exchange = self._exchange(target.id)
        rumor = rumors.rumor_of(self.id, text, exchange) if rumors is not None else exchange
        key = rumor or uid
        bar_drop = max(0.0, self.engine.rapport.interest(target.id, self.id, key)) * self.config.near_miss_band
        place = self.town.place_of(target.id)
        utterance = Utterance(uid, NEWCOMER, "A newcomer", place, text, None, "whispered to you", conversation=rumor)
        self.engine.facts.record(utterance)
        published_at = datetime.now(UTC)
        since = (published_at - timedelta(seconds=self.config.check_lookback_seconds)).isoformat()
        if rumors is not None:
            rumors.open(uid, text, published_at, target.id, target.name, rumor, player=self.id, place=place)
        try:
            await self.engine.deliver(utterance, [target.id])
        except Exception:
            log.exception("whisper to %s failed", target.id)
            if rumors is not None:
                rumors.discard(uid)
            return NOT_CARRIED
        self.whispered_at = self._said_at[target.id] = time.time()
        if self.game is not None and rumor is None:
            self.game.moved(self.id)
        if utterance.place != self.place:
            self._move(utterance.place)
        self.engine.suspicion.bump(
            target.id, self.id, self.config.suspicion_whisper_bump, "whispered to them directly", uid,
        )
        self.feed.on_whisper_status(uid, target.id, target.name, "checking")
        found, _ = await asyncio.gather(self.check(target, utterance, since, hint_need, bar_drop), self._card(uid))
        if found is None:
            self.feed.on_whisper_status(uid, target.id, target.name, "not_carried")
            return NOT_CARRIED
        marked = self._marks_of(target.id)
        if marked:
            self._track(asyncio.create_task(self._worked_on(target, text, marked)))
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
            self.engine.rapport.move(target.id, self.id, key, self.config.interest_hit)
            self._threads.pop(target.id, None)
            self._held[target.id] = (uid, key)
            if not self.engine.hear_directly(target.id, hit):
                return None
            until = self.engine.cooldown_ends(target.id)
            if until > time.time():
                self.feed.on_whisper_status(uid, target.id, target.name, "busy", until=until)
            else:
                self.feed.on_whisper_status(uid, target.id, target.name, "heard")
        else:
            closest = max(near_misses, key=lambda n: n[1].clearance, default=None)
            interest = self.engine.rapport.move(target.id, self.id, key, self._miss_delta(closest))
            continues = interest > self.config.interest_end
            state = "near_miss" if closest else "brushed_off"
            self.feed.on_whisper_status(uid, target.id, target.name, state, continues=continues)
            open_now = self.engine.conversations.get((target.id, self.id))
            if utterance.conversation and open_now and open_now["id"] == utterance.conversation:
                self.engine.end_conversation(target.id, self.id)
            if not continues:
                self._lose_patience(target, key)
                return None
            concern = closest[0] if closest else self._next_concern(target.id)
            self._extend_thread(target.id, uid, concern)
            mood = self.engine.rapport.mood(target.id, self.id, key, greet=self.engine.rapport.greets(target.id, self.id, key))
            task = asyncio.create_task(
                self._hint(target.id, target.name, uid, key, concern, state, text, continues, mood),
            )
            self._track(task)
        return None

    def _miss_delta(self, closest: tuple[str, Match] | None) -> float:
        """How a missed whisper moves interest: up for a near miss, more the closer it came to the bar; down for a
        brush-off."""
        if closest is None:
            return self.config.interest_brush_off
        closeness = 1 + closest[1].clearance / self.config.near_miss_band
        low, high = self.config.interest_near_min, self.config.interest_near_max
        return low + (high - low) * closeness

    def _lose_patience(self, target: Villager, rumor: str) -> None:
        """The villager has had enough of the rumor: she ends the exchange, likes the player less and suspects them a
        little, then takes their leave in the background."""
        self.part(target.id)
        self.engine.rapport.sour(target.id, self.id, self.config.rapport_lost_patience)
        self.engine.rapport.forget(target.id, self.id, rumor)
        self.engine.suspicion.bump(
            target.id, self.id, self.config.suspicion_whisper_bump / 2, "lost patience with you",
        )
        self._track(asyncio.create_task(self._take_leave(target)))

    async def _take_leave(self, target: Villager) -> None:
        """Show the villager taking their leave of the player, with their parting words if any."""
        line = None
        if self.writer is not None:
            try:
                line = await self.writer.lose_patience(target) or None
            except Exception:
                log.exception("lose-patience line for %s failed", target.id)
        self.feed.on_storm_off(target.id, target.name, line, player=self.id, verb="takes their leave")

    def _marks_of(self, villager_id: str) -> list[Goal]:
        """This player's unmet claim goals about someone that the villager's belief counts toward."""
        return [g for g in self.goals if g.claim_text and g.subject and villager_id in g.audience and g.id not in self.met]

    async def _worked_on(self, target: Villager, text: str, goals: list[Goal]) -> None:
        """Raise the mark's suspicion once if the whisper plainly tries to turn them against a goal's subject."""
        question = "Is the newcomer plainly trying to turn this villager against the person the claim is about?"
        try:
            with charging(self.id):
                for goal in goals:
                    state = {
                        "villager": f"{target.name}, {target.occupation}. {target.personality}",
                        "claim": goal.claim_text,
                        "about": self.town.name_of(goal.subject) or "",
                        "newcomer said": text,
                    }
                    response = await self.engine.jev.system_one(state=state, questions={"q": Noul(instructions=question)})
                    if response.nouls["q"].noul > 0.5:
                        self.engine.suspicion.bump(
                            target.id, self.id, self.config.suspicion_whisper_bump, "worked on them",
                        )
                        return
        except Exception:
            log.exception("judging whether %s was worked on failed", target.id)

    async def ask(self, villager_id: str, topic: str) -> str | None:
        """Ask the villager what folk have been saying about the topic; returns why it could not be done, if it couldn't.

        A villager turning the question away comes back as a Refusal. The model call is charged to this player.
        """
        with charging(self.id):
            return await self._ask(villager_id, topic)

    async def _ask(self, villager_id: str, topic: str) -> str | None:
        if self.over():
            return GAME_OVER
        if villager_id not in self.town:
            gone = self.town.name_of(villager_id)
            return f"{gone} has left town." if gone else "No one in town has that id."
        topic = topic.strip()
        if not topic:
            return "Ask about what? Try: the mill"
        if len(topic) > MAX_WHISPER_CHARS:
            return f"Too long: a question is at most {MAX_WHISPER_CHARS} characters."
        villager = self.town.get(villager_id)
        place = self.town.place_of(villager_id)
        if place != self.place:
            return f"{villager.name} is at the {place}. Walk there to ask them."
        if self.engine.suspicion.score(villager_id, self.id) >= self.config.refuse_threshold:
            self.feed.on_refusal(villager_id, villager.name, await self._turn_away(villager), player=self.id)
            return Refusal(f"{villager.name} turns away from you.")
        self.engine.suspicion.bump(villager_id, self.id, self.config.suspicion_whisper_bump / 2, "asked questions")
        recalled = await self.engine.recall_heard(villager_id, topic, "")
        heard = [u for u in map(self._tellable, recalled) if u is not None][:ASK_LINES]
        line = await self._answer(villager, topic, heard)
        self.feed.on_answer(villager_id, villager.name, topic, line, player=self.id)
        await self.engine.hints.add(self.id, villager_id, line)
        return None

    def _tellable(self, m: Match) -> Utterance | None:
        """The fact behind a recalled line, unless it would give away another newcomer."""
        fact = self.engine.facts.get(m.data.get("id"))
        if fact is None:
            return None
        if self.lineage.player_of(fact.id) == self.id or fact.about == self.id:
            return fact
        if fact.names_newcomer or fact.speaker == NEWCOMER or fact.about is not None:
            return None
        return fact

    async def _answer(self, villager: Villager, topic: str, heard: list[Utterance]) -> str:
        """The villager's answer from the writer, or a plain line when there is none or it fails."""
        if self.writer is not None:
            try:
                if line := await self.writer.answer(
                    villager, topic, [f"{u.speaker_name} at the {u.place}: {u.text}" for u in heard],
                    neighbours(self.town, villager, topic),
                ):
                    return line
            except Exception:
                log.exception("answer for %s failed", villager.id)
        if heard:
            return f"{villager.name} heard {heard[0].speaker_name} talking about it at the {heard[0].place}."
        return f"{villager.name} hasn't heard anything about that."

    def _exchange(self, villager_id: str) -> str | None:
        """The rumor of the player's open exchange with the villager: their conversation, a missed whisper's thread, or
        a landed whisper the villager has not acted on yet."""
        if conversation := self.engine.conversations.get((villager_id, self.id)):
            return conversation["id"]
        if thread := self._thread(villager_id):
            return thread["id"]
        held = self._held.get(villager_id)
        if held is None:
            return None
        uid, rumor = held
        for wid, _ in self.feed.listening(villager_id):
            if wid == uid:
                return rumor
        return None

    def typing(self) -> None:
        """The player is writing, which keeps whoever they are talking with where they are."""
        self._typed_at = time.time()

    def holds(self, villager_id: str) -> bool:
        """Whether the villager stays put for this player: they share a place and an open exchange, and the player
        spoke to them or typed within talk_hold_seconds."""
        said = self._said_at.get(villager_id)
        return (
            not self.over() and said is not None
            and time.time() - max(said, self._typed_at) < self.config.talk_hold_seconds
            and villager_id in self.town and self.town.place_of(villager_id) == self.place
            and self._exchange(villager_id) is not None
        )

    def annoyed(self, villager_id: str) -> bool:
        """Whether the villager has had enough of this player: wary of them, or tired of their open rumor."""
        if self.engine.suspicion.score(villager_id, self.id) >= self.config.warn_threshold:
            return True
        exchange = self._exchange(villager_id)
        if exchange is None:
            return False
        rumors = self.engine.rumors
        # With an exchange given, rumor_of resolves it to its card and never reads the text.
        rumor = rumors.rumor_of(self.id, "", exchange) if rumors is not None else exchange
        return self.engine.rapport.interest(villager_id, self.id, rumor) <= self.config.interest_annoyed

    def part(self, villager_id: str) -> None:
        """End every exchange this player has open with the villager."""
        self._threads.pop(villager_id, None)
        self._held.pop(villager_id, None)
        self.engine.end_conversation(villager_id, self.id)

    def _thread(self, villager_id: str) -> dict | None:
        """The villager's open thread, dropped once the player has neither whispered on it nor typed for
        talk_hold_seconds."""
        thread = self._threads.get(villager_id)
        if thread and time.time() - max(thread["at"], self._typed_at) >= self.config.talk_hold_seconds:
            del self._threads[villager_id]
            return None
        return thread

    def _extend_thread(self, villager_id: str, uid: str, concern: str | None) -> None:
        """Open a thread on a missed whisper, or carry the open one on to this concern."""
        thread = self._thread(villager_id)
        self._threads[villager_id] = {"id": thread["id"] if thread else uid, "need": concern, "at": time.time()}

    async def check(
        self, target: Villager, utterance: Utterance, since: str, hint_need: str | None = None, bar_drop: float = 0.0,
    ) -> list[tuple[str, Match]] | None:
        """The whisper's (need, match) pairs within the near-miss band of each need's bar, None on error.

        Each match carries its clearance against the bar: below zero is a near miss.
        The hinted need is held to its bare baseline plus the hint margin; the rest to their full threshold.
        Every bar is lowered by bar_drop.
        """
        key = f"utt:{utterance.id}"
        project = mind_project(self.config.session, target.id)
        bars = {need: self._bar(need, need == hint_need) - bar_drop for need in target.needs}
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

    async def _card(self, uid: str) -> None:
        if self.engine.rumors is not None:
            await self.engine.rumors.publish(uid)

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
        self, villager_id: str, name: str, uid: str, rumor: str, concern: str | None, state: str, text: str,
        continues: bool, mood: str,
    ) -> None:
        """Add the brush-off or lean-in line, or nothing, to a missed whisper's status."""
        if self.over():
            return
        try:
            if state == "near_miss":
                line = await self._go_on(villager_id, text, mood)
            else:
                line = await self._brush_off(villager_id, concern, mood)
        except Exception:
            log.exception("%s line for %s failed", state, villager_id)
            line = None
        if line:
            self.engine.rapport.greeted(villager_id, self.id, rumor)
        if self.over():
            return
        self.feed.on_whisper_status(uid, villager_id, name, state, hint=line, continues=continues)
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

    async def _brush_off(self, villager_id: str, concern: str | None, mood: str = "") -> str | None:
        """An in-character line hinting at a concern, or None for a plain shrug."""
        if self.writer is None or concern is None or villager_id not in self.town:
            return None
        for _ in range(2):
            try:
                line = await self.writer.brush_off(self.town.get(villager_id), concern, mood)
            except Exception:
                log.exception("brush-off for %s failed", villager_id)
                return None
            if line and not echoes(line, concern):
                return line
        return None

    async def _go_on(self, villager_id: str, text: str, mood: str = "") -> str | None:
        """An in-character line urging the newcomer on, or None for a plain lean-in."""
        if self.writer is None or villager_id not in self.town:
            return None
        try:
            return await self.writer.go_on(self.town.get(villager_id), text, mood) or None
        except Exception:
            log.exception("go-on for %s failed", villager_id)
            return None
