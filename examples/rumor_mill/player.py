"""The player's whispers: validation, delivery, and the shrug timer."""

import asyncio
import logging

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.town import PLAYER_ID, Town

log = logging.getLogger(__name__)

MAX_WHISPER_CHARS = 500


class Player:
    def __init__(self, engine, town: Town, lineage: Lineage, ids: Ids, feed, config: Config):
        self.engine = engine
        self.town = town
        self.lineage = lineage
        self.ids = ids
        self.feed = feed
        self.config = config
        self._shrugs: dict[tuple[str, str], asyncio.Task] = {}

    async def whisper(self, name: str, text: str) -> str | None:
        """Whisper text to the villager called name; returns why it could not be done, if it couldn't."""
        name, text = name.strip(), text.strip()
        if not name or not text:
            return "Whisper to whom, and what? Try: Marta: the miller waters his flour"
        if len(text) > MAX_WHISPER_CHARS:
            return f"Too long: a whisper is at most {MAX_WHISPER_CHARS} characters."
        matches = self.town.find(name)
        if not matches:
            gone = [v for v in self.town.departed if v.name.lower() == name.lower()]
            return f"{gone[0].name} has left town." if gone else f"No one in town is called {name}."
        if len(matches) > 1:
            return f"Which {name}? " + ", ".join(v.name for v in matches)
        target = matches[0]
        uid = self.ids.next("w")
        self.lineage.record(uid, None, player=True)
        utterance = Utterance(
            uid, PLAYER_ID, "The newcomer", self.town.place_of(target.id), text, None, "whispered to you",
        )
        shrug = asyncio.create_task(self._shrug(target.id, target.name, uid))
        self._shrugs[(target.id, uid)] = shrug
        try:
            await self.engine.deliver(utterance, [target.id])
        except Exception:
            log.exception("whisper to %s failed", target.id)
            shrug.cancel()
            self._shrugs.pop((target.id, uid), None)
            return "The whisper didn't carry; try again."
        return None

    def note_heard(self, villager_id: str, matches: list[Match]) -> None:
        """Cancel the shrug timer when a match is a whisper to this villager."""
        for m in matches:
            task = self._shrugs.pop((villager_id, m.data.get("id")), None)
            if task:
                task.cancel()

    def close(self) -> None:
        for task in self._shrugs.values():
            task.cancel()
        self._shrugs.clear()

    async def _shrug(self, villager_id: str, name: str, uid: str) -> None:
        await asyncio.sleep(self.config.cooldown_seconds + self.config.shrug_seconds)
        self._shrugs.pop((villager_id, uid), None)
        self.feed.on_shrug(villager_id, name, uid)
