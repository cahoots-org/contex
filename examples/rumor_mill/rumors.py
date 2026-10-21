"""Rumor cards: each whisper, the retellings still carrying it, and how far it has spread."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.models import Utterance

log = logging.getLogger(__name__)

# ponytail: Contex caps top_k at 100, so a whisper followed by 100 closer ones can read as off topic.
TOP_K = 100


@dataclass
class Card:
    uid: str
    text: str
    target_id: str
    target_name: str
    published_at: datetime
    baseline: float | None = None
    # The first baseline measurement, started when the card is.
    measuring: asyncio.Future | None = None
    hops: list[dict] = field(default_factory=list)
    heard: set[str] = field(default_factory=set)
    # When each listener last heard an on-topic hop.
    told_at: dict[str, float] = field(default_factory=dict)
    drifted: bool = False


class Rumors:
    def __init__(self, contex: ContexClient, baselines: Baselines, config: Config):
        self.contex = contex
        self.baselines = baselines
        self.config = config
        self.project = f"rumor-mill:{config.session}:rumors"
        self._cards: dict[str, Card] = {}

    async def add(self, whisper_uid: str, text: str, published_at: datetime, target_id: str, target_name: str) -> None:
        """Start the whisper's card and its baseline measurement, then publish its words; failures are logged."""
        card = self._cards[whisper_uid] = Card(whisper_uid, text, target_id, target_name, published_at)
        card.heard.add(target_id)
        card.measuring = asyncio.ensure_future(self._measure(whisper_uid, text))
        try:
            await self.contex.publish(self.project, f"rumor:{whisper_uid}", {"id": whisper_uid, "text": text})
        except Exception:
            log.warning("rumor words for %s were not published", whisper_uid)
            log.debug("rumor publish failure", exc_info=True)

    async def _measure(self, whisper_uid: str, text: str) -> float | None:
        try:
            return await self.baselines.measure(text)
        except Exception:
            log.warning("baseline for %s failed", whisper_uid)
            log.debug("baseline failure", exc_info=True)
            return None

    async def on_topic(self, root_uid: str, text: str) -> bool:
        """Whether text still carries the whisper root_uid; any failure counts as off topic."""
        card = self._cards.get(root_uid)
        if card is None:
            log.warning("no rumor card to check %s against", root_uid)
            return False
        since = card.published_at - timedelta(seconds=self.config.check_lookback_seconds)
        try:
            if card.baseline is None and not card.measuring.cancelled():
                card.baseline = await asyncio.shield(card.measuring)
            if card.baseline is None:
                card.baseline = await self.baselines.measure(card.text)
            found = await self.contex.query(self.project, text, top_k=TOP_K, threshold=0, since=since.isoformat())
        except Exception:
            log.warning("topic check for %s failed", root_uid)
            log.debug("topic check failure", exc_info=True)
            return False
        key = f"rumor:{root_uid}"
        similarity = next((m.similarity for m in found if m.data_key == key and m.similarity is not None), None)
        bar = card.baseline + self.config.on_topic_margin
        log.debug("topic check for %s: %s against %.3f", root_uid, similarity, bar)
        return similarity is not None and similarity >= bar

    def record(self, root_uid: str, hop: Utterance, listeners: dict[str, str], on_topic: bool) -> bool:
        """Note a retelling of root_uid heard by listeners (id to name); True when it first turns the card."""
        card = self._cards.get(root_uid)
        if card is None:
            return False
        if not on_topic:
            drifted, card.drifted = card.drifted, True
            return not drifted
        card.hops.append({"uid": hop.id, "speaker": hop.speaker_name, "listeners": list(listeners.values()),
                          "text": hop.text})
        card.heard.update(listeners)
        now = time.time()
        card.told_at.update(dict.fromkeys(listeners, now))
        return False

    def close(self) -> None:
        """Cancel any baseline measurement still running."""
        for card in self._cards.values():
            card.measuring.cancel()

    def cards(self) -> list[dict]:
        """Every card, oldest first, with how many heard it and how many heard it in the last game day."""
        cutoff = time.time() - self.config.day_seconds
        return [
            {"uid": c.uid, "text": c.text, "target_id": c.target_id, "target_name": c.target_name, "hops": c.hops,
             "heard": len(c.heard), "still": sum(at >= cutoff for at in c.told_at.values()), "drifted": c.drifted}
            for c in self._cards.values()
        ]
