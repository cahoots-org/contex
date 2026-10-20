"""The player's whispers: validation, delivery, and whether each one landed."""

import asyncio
import logging
import re
import time
from datetime import UTC, datetime, timedelta

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.game import Game
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.mind import mind_project
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.town import PLAYER_ID, Town

log = logging.getLogger(__name__)

MAX_WHISPER_CHARS = 500
ECHO_WORDS = 4
GAME_OVER = "The game is over."
NOT_CARRIED = "The whisper didn't carry; try again."


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
    ):
        self.engine = engine
        self.town = town
        self.lineage = lineage
        self.ids = ids
        self.feed = feed
        self.config = config
        self.writer = writer
        self.game = game
        self._brush_offs: set[asyncio.Task] = set()
        self._hinted: dict[str, set[int]] = {}

    async def whisper(self, name: str, text: str) -> str | None:
        """Whisper text to the villager called name; returns why it could not be done, if it couldn't.

        A villager turning the whisper away comes back as a Refusal.
        """
        if self._over():
            return GAME_OVER
        name = name.strip()
        if not name or not text.strip():
            return "Whisper to whom, and what? Try: Marta: the miller waters his flour"
        matches = self.town.find(name)
        if not matches:
            gone = [v for v in self.town.departed if v.name.lower() == name.lower()]
            return f"{gone[0].name} has left town." if gone else f"No one in town is called {name}."
        if len(matches) > 1:
            return f"Which {name}? " + ", ".join(v.name for v in matches)
        return await self.whisper_to(matches[0].id, text)

    async def whisper_to(self, villager_id: str, text: str) -> str | None:
        """Whisper text to the villager with this id; returns why it could not be done, if it couldn't.

        A villager turning the whisper away comes back as a Refusal.
        """
        if self._over():
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
        if self.engine.suspicion.score(target.id) >= self.config.refuse_threshold:
            self.engine.suspicion.bump(target.id, self.config.suspicion_whisper_bump, "tried to whisper again")
            self.feed.on_refusal(target.id, target.name, await self._turn_away(target))
            return Refusal(f"{target.name} turns away from you.")
        uid = self.ids.next("w")
        self.lineage.record(uid, None, player=True)
        utterance = Utterance(
            uid, PLAYER_ID, "The newcomer", self.town.place_of(target.id), text, None, "whispered to you",
        )
        since = (datetime.now(UTC) - timedelta(seconds=self.config.check_lookback_seconds)).isoformat()
        try:
            await self.engine.deliver(utterance, [target.id])
        except Exception:
            log.exception("whisper to %s failed", target.id)
            return NOT_CARRIED
        self.engine.suspicion.bump(target.id, self.config.suspicion_whisper_bump, "whispered to them directly", uid)
        self.feed.on_whisper_status(uid, target.id, target.name, "checking")
        hits = await self.check(target, utterance, since)
        if hits is None:
            self.feed.on_whisper_status(uid, target.id, target.name, "not_carried")
            return NOT_CARRIED
        if hits:
            if not self.engine.hear_directly(target.id, Match(f"utt:{uid}", utterance.to_data(self.engine.day), max(hits))):
                return None
            until = self.engine.cooldown_ends(target.id)
            if until > time.time():
                self.feed.on_whisper_status(uid, target.id, target.name, "busy", until=until)
            else:
                self.feed.on_whisper_status(uid, target.id, target.name, "heard")
        else:
            self.feed.on_whisper_status(uid, target.id, target.name, "brushed_off")
            task = asyncio.create_task(self._hint(target.id, target.name, uid))
            self._brush_offs.add(task)
            task.add_done_callback(self._brush_offs.discard)
        return None

    async def check(self, target: Villager, utterance: Utterance, since: str) -> list[float] | None:
        """The similarities with which the whisper clears the target's needs; empty for a miss, None on error."""
        key = f"utt:{utterance.id}"
        project = mind_project(self.config.session, target.id)
        try:
            found = await asyncio.gather(*(
                self.engine.contex.query(
                    project, need, top_k=self.config.top_k, since=since, threshold=self.engine.baselines.threshold(need),
                )
                for need in target.needs
            ))
        except Exception:
            log.exception("whisper check for %s failed", target.id)
            return None
        return [
            m.similarity for need, matches in zip(target.needs, found) for m in matches
            if m.data_key == key and m.similarity is not None and self.engine.baselines.matches(need, m.similarity)
        ]

    def _over(self) -> bool:
        return self.game is not None and self.game.finished.is_set()

    def close(self) -> None:
        for task in self._brush_offs:
            task.cancel()
        self._brush_offs.clear()

    async def _hint(self, villager_id: str, name: str, uid: str) -> None:
        """Add the brush-off line, or a plain shrug, to a missed whisper's status."""
        if self._over():
            return
        try:
            line = await self._brush_off(villager_id)
        except Exception:
            log.exception("brush-off for %s failed", villager_id)
            line = None
        if not self._over():
            self.feed.on_whisper_status(uid, villager_id, name, "brushed_off", hint=line)

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

    async def _brush_off(self, villager_id: str) -> str | None:
        """An in-character line hinting at a concern, or None for a plain shrug."""
        if self.writer is None or villager_id not in self.town:
            return None
        concern = self._next_concern(villager_id)
        if concern is None:
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
