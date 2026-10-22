"""Rumor cards: each whisper, the retellings still carrying it, and how far it has spread."""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from examples.rumor_mill.calibration import Baselines
from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import ContexClient
from examples.rumor_mill.feed import Event
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.scenes import Scene
from examples.rumor_mill.town import NEWCOMER

log = logging.getLogger(__name__)

# ponytail: Contex caps top_k at 100, so a whisper followed by 100 closer ones can read as off topic.
TOP_K = 100
FRONTIER_STATES = ("heard", "busy", "answered", "uncared", "worn", "knew", "kept", "warned", "drifted", "passed")
# States a later check or outcome may still replace.
OPEN_STATES = ("heard", "busy", "answered")
# The frontier state each whisper status gives the whisper's target.
TARGET_STATES = {
    "checking": "heard", "heard": "heard", "busy": "busy", "answered": "answered", "brushed_off": "uncared",
    "near_miss": "uncared", "kept": "kept", "moved_on": "kept", "warned": "warned", "took_the_bait": "passed",
}
STOP_LINES = {
    "worn": "It wore out after {tellings}.",
    "knew": "Everyone who heard it had heard it before.",
    "uncared": "It stopped with {who}: it didn't touch anything they care about.",
    "warned": "It stopped with {who}, who warned others about you.",
    "kept": "It stopped with {who}, who kept it quiet.",
    "drifted": "It turned into another story with {who}.",
    "crowd": "It stopped at the {place}: {heard} heard it and no one passed it on.",
}
# How a tie between the commonest ends is broken.
STOP_ORDER = ("worn", "uncared", "warned", "drifted", "kept")


@dataclass
class Card:
    uid: str
    text: str
    target_id: str
    target_name: str
    published_at: datetime
    player: str = ""
    baseline: float | None = None
    # The first baseline measurement, started when the card is.
    measuring: asyncio.Future | None = None
    hops: list[dict] = field(default_factory=list)
    heard: set[str] = field(default_factory=set)
    # When each listener last heard an on-topic hop.
    told_at: dict[str, float] = field(default_factory=dict)
    drifted: bool = False
    # The newcomer's replies and the villager's answers after the first whisper, in order.
    conversation: list[dict] = field(default_factory=list)
    # For a reply whisper, the first whisper whose card shows it.
    part_of: str | None = None
    # A topic is measured and checked against like a whisper but never listed.
    topic_only: bool = False
    # Who holds the rumor now, by villager id: name, state, at, until, told, scene, before and depth.
    frontier: dict[str, dict] = field(default_factory=dict)


def _entry(name: str, state: str, **extra) -> dict:
    return {"name": name, "state": state, "at": time.time(), "until": None, "told": [], "scene": None, "before": False,
            "depth": 0, **extra}


def _who(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{len(names)} people"


class Rumors:
    def __init__(self, contex: ContexClient, baselines: Baselines, config: Config):
        self.contex = contex
        self.baselines = baselines
        self.config = config
        self.project = f"rumor-mill:{config.session}:rumors"
        self._cards: dict[str, Card] = {}

    async def add(
        self, whisper_uid: str, text: str, published_at: datetime, target_id: str, target_name: str,
        conversation: str | None = None, *, player: str,
    ) -> None:
        """Start the whisper's card and its baseline measurement, then publish its words; failures are logged.

        A reply in a conversation is shown on the conversation's card instead of its own.
        """
        card = self._cards[whisper_uid] = Card(whisper_uid, text, target_id, target_name, published_at, player)
        if conversation in self._cards:
            card.part_of = conversation
            self.add_line(conversation, Utterance(whisper_uid, NEWCOMER, "A newcomer", "", text, None))
        card.heard.add(target_id)
        await self._start(card)

    async def add_topic(self, uid: str, text: str, published_at: datetime) -> None:
        """Measure and publish a topic so on_topic can check lines against it; it is never listed in cards."""
        card = self._cards[uid] = Card(uid, text, "", "", published_at, topic_only=True)
        await self._start(card)

    async def _start(self, card: Card) -> None:
        card.measuring = asyncio.ensure_future(self._measure(card.uid, card.text))
        try:
            await self.contex.publish(self.project, f"rumor:{card.uid}", {"id": card.uid, "text": card.text})
        except Exception:
            log.warning("rumor words for %s were not published", card.uid)
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

    def add_line(self, conversation: str, utterance: Utterance) -> None:
        """Append a line to the conversation shown on that whisper's card."""
        if card := self._cards.get(conversation):
            card.conversation.append(
                {"uid": utterance.id, "speaker_id": utterance.speaker, "speaker": utterance.speaker_name, "text": utterance.text}
            )

    def record(self, root_uid: str, hop: Utterance, listeners: dict[str, str], on_topic: bool) -> bool:
        """Note a retelling of root_uid heard by listeners (id to name); True when it first turns the card."""
        card = self._cards.get(root_uid)
        if card is None:
            return False
        shown = self._cards.get(card.part_of, card)
        teller = shown.frontier.get(hop.speaker)
        if not on_topic:
            if hop.speaker in shown.heard and (teller is None or teller["state"] != "passed"):
                shown.frontier[hop.speaker] = _entry(hop.speaker_name, "drifted", scene=teller and teller["scene"])
            drifted, shown.drifted = shown.drifted, True
            return not drifted
        shown.hops.append({"uid": hop.id, "speaker_id": hop.speaker, "speaker": hop.speaker_name,
                           "listeners": list(listeners.values()), "ids": list(listeners), "place": hop.place,
                           "text": hop.text})
        told = teller["told"] if teller and teller["state"] == "passed" else []
        shown.frontier[hop.speaker] = _entry(hop.speaker_name, "passed", told=told, scene=teller and teller["scene"])
        if len(listeners) == 1:
            (vid, name), = listeners.items()
            told.append(name)
            held = shown.frontier.get(vid)
            if held is None or held["state"] != "passed":
                shown.frontier[vid] = _entry(name, "heard", before=vid in shown.heard, scene=held and held["scene"])
        shown.heard.update(listeners)
        shown.told_at.update(dict.fromkeys(listeners, time.time()))
        return False

    def on_event(self, event: Event) -> None:
        """Feed sink: a whisper status sets where the whisper's target stands on its card."""
        if event.kind != "whisper_status" or event.data["state"] not in (*TARGET_STATES, "not_carried"):
            return
        d = event.data
        if d["state"] != "not_carried":
            self.reached(d["whisper_id"], d["villager_id"], d["name"], TARGET_STATES[d["state"]], until=d.get("until"))
            return
        card = self._shown(d["whisper_id"])
        if card is not None and card.frontier.get(d["villager_id"], {}).get("state") in OPEN_STATES:
            del card.frontier[d["villager_id"]]

    def reached(self, uid: str, vid: str, name: str, state: str, **extra) -> None:
        """Set where vid stands with the rumor uid belongs to, unless an outcome already settled it.

        A listener who heard it before and does not care, or finds it worn, already knew it.
        """
        card = self._shown(uid)
        if card is None:
            return
        held = card.frontier.get(vid)
        if held is not None and held["state"] not in OPEN_STATES:
            return
        before = bool(held and held["before"])
        if state in ("uncared", "worn") and before:
            state = "knew"
        card.frontier[vid] = _entry(name, state, before=before, scene=held and held["scene"], **extra)

    def in_scene(self, uid: str, scene: Scene, names: dict[str, str]) -> None:
        """Mark the scene's people on the rumor's frontier while it runs, and clear the marks once it ends."""
        card = self._shown(uid)
        if card is None:
            return
        for held in card.frontier.values():
            if held["scene"] and held["scene"]["id"] == scene.id:
                held["scene"] = None
        if scene.ended:
            return
        mark = {"id": scene.id, "tone": "argument" if scene.heated else "conversation", "place": scene.place}
        for vid in sorted(scene.participants):
            card.frontier.setdefault(vid, _entry(names.get(vid, vid), "heard"))["scene"] = mark

    def _shown(self, uid: str) -> Card | None:
        """The card that shows uid's whisper."""
        card = self._cards.get(uid)
        return card and self._cards.get(card.part_of, card)

    def carries(self, root_uid: str, uid: str) -> bool:
        """Whether uid is an on-topic hop shown on the card that root_uid belongs to."""
        card = self._cards.get(root_uid)
        if card is None:
            return False
        shown = self._cards.get(card.part_of, card)
        return any(hop["uid"] == uid for hop in shown.hops)

    def told_only(self, root_uid: str, uid: str, vid: str) -> bool:
        """Whether uid is an on-topic hop on root_uid's card told to vid alone."""
        card = self._shown(root_uid)
        return card is not None and any(hop["uid"] == uid and hop["ids"] == [vid] for hop in card.hops)

    def close(self) -> None:
        """Cancel any baseline measurement still running."""
        for card in self._cards.values():
            card.measuring.cancel()

    def cards(self, player: str) -> list[dict]:
        """The player's cards but replies, oldest first, with how many heard it and how many heard it in the last game day,
        who holds it now, why it stopped, and how it touched each villager."""
        cutoff = time.time() - self.config.day_seconds
        return [
            {"uid": c.uid, "text": c.text, "target_id": c.target_id, "target_name": c.target_name, "hops": c.hops,
             "heard": len(c.heard), "still": sum(at >= cutoff for at in c.told_at.values()), "drifted": c.drifted,
             "conversation": c.conversation, "frontier": self._frontier(c), "stopped": self._stopped(c, cutoff),
             "touched": self._touched(c)}
            for c in self._cards.values() if c.player == player and c.part_of is None and not c.topic_only
        ]

    def _frontier(self, card: Card) -> list[dict]:
        held = sorted(card.frontier.items(), key=lambda item: item[1]["at"])
        return [
            {"id": vid, "name": e["name"], "state": e["state"], "until": e["until"], "told": list(e["told"]),
             "crowd": self._crowd(card, vid),
             "scene": e["scene"] and {"tone": e["scene"]["tone"], "place": e["scene"]["place"]}}
            for vid, e in held
        ]

    def _crowd(self, card: Card, vid: str) -> dict | None:
        """The teller's latest room hop: where, how many heard it, and how many of them passed it on."""
        hop = next((h for h in reversed(card.hops) if h["speaker_id"] == vid and len(h["ids"]) > 1), None)
        if hop is None:
            return None
        passed = sum(card.frontier.get(i, {}).get("state") == "passed" for i in hop["ids"])
        return {"place": hop["place"], "heard": len(hop["ids"]), "passed": passed}

    def _stopped(self, card: Card, cutoff: float) -> str | None:
        """Why the rumor stopped, or None while anyone may still pass it on.

        Someone who heard it more than a game day ago and did nothing kept it quiet.
        """
        held = card.frontier.values()
        if not held or any(e["scene"] or (e["state"] in OPEN_STATES and e["at"] >= cutoff) for e in held):
            return None
        ends = [e for e in held if e["state"] != "passed"]
        if not ends:
            room = next((h for h in reversed(card.hops) if len(h["ids"]) > 1), None)
            if room is None:
                return None
            return STOP_LINES["crowd"].format(place=room["place"], heard=len(room["ids"]))
        if all(e["state"] == "knew" for e in ends):
            return STOP_LINES["knew"]
        states = {"knew": "uncared", **dict.fromkeys(OPEN_STATES, "kept")}
        counts = Counter(states.get(e["state"], e["state"]) for e in ends)
        state = max(STOP_ORDER, key=lambda s: (counts[s], -STOP_ORDER.index(s)))
        names = [e["name"] for e in ends if states.get(e["state"], e["state"]) == state]
        tellings = max((e["depth"] for e in ends if e["state"] == "worn"), default=0)
        return STOP_LINES[state].format(who=_who(names), tellings="1 telling" if tellings == 1 else f"{tellings} tellings")

    def _touched(self, card: Card) -> dict[str, str]:
        touched = dict.fromkeys(sorted(card.heard), "heard")
        for vid, e in card.frontier.items():
            if e["state"] == "passed":
                touched[vid] = "passed"
            if e["scene"]:
                touched[vid] = "scene"
        return touched
