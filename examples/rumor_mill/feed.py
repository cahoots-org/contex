"""The feed: typed events about the game, sent to every sink and kept for replay."""

import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from examples.rumor_mill.goals import Goal
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.town import PLAYER_ID, Town

REPLAY_EVENTS = 500


@dataclass(frozen=True)
class Event:
    id: int
    at: float
    kind: str
    # What the terminal prints; empty when it prints nothing.
    line: str
    data: dict

    def to_json(self) -> dict:
        return {"id": self.id, "at": self.at, "kind": self.kind, **self.data}


Sink = Callable[[Event], None]


def terminal(out: Callable[[str], None]) -> Sink:
    def sink(event: Event) -> None:
        if event.line:
            out(event.line)

    return sink


class Feed:
    def __init__(self, lineage: Lineage, town: Town, out: Callable[[str], None] | None = print):
        self.lineage = lineage
        self.town = town
        self.sinks: list[Sink] = [terminal(out)] if out else []
        self.recent: deque[Event] = deque(maxlen=REPLAY_EVENTS)
        self._ids = itertools.count(1)

    def emit(self, kind: str, line: str = "", /, **data) -> Event:
        event = Event(next(self._ids), time.time(), kind, line, data)
        self.recent.append(event)
        for sink in self.sinks:
            sink(event)
        return event

    def say(self, line: str) -> None:
        self.emit("system", line, text=line)

    def on_progress(self, goal: Goal, fraction: float) -> None:
        percent = int(fraction * 100)
        self.emit("goal", f'Goal "{goal.text}": {percent}%', goal_id=goal.id, text=goal.text, percent=percent, met=False)

    def on_met(self, goal: Goal) -> None:
        self.emit("goal", f'Goal met: "{goal.text}"', goal_id=goal.id, text=goal.text, percent=100, met=True)

    def on_shrug(self, villager_id: str, name: str, whisper_id: str, line: str | None = None) -> None:
        text = f'{name} brushes you off: "{line}"' if line else f"{name} shrugs it off."
        self.emit("shrug", text, villager_id=villager_id, name=name, uid=whisper_id, hint=line)

    def on_refusal(self, villager_id: str, name: str, line: str | None = None) -> None:
        text = f'{name} turns away from you: "{line}"' if line else f"{name} turns away from you."
        self.emit("refusal", text, villager_id=villager_id, name=name, line=line)

    def on_warning(self, speaker_id: str, speaker_name: str, target_id: str, target_name: str) -> None:
        self.emit(
            "warning", f"{speaker_name} warned {target_name} about you.",
            speaker_id=speaker_id, speaker=speaker_name, target_id=target_id, target=target_name,
        )

    def on_delivery(self, utterance: Utterance, listener_ids: list[str]) -> None:
        """Emit the player's whisper, a warning, a hop of a player-rooted rumor, or a town announcement."""
        u = utterance
        names = [self.town.get(i).name for i in listener_ids if i in self.town]
        if u.speaker == PLAYER_ID:
            if listener_ids:
                self.emit("whisper", uid=u.id, target_id=listener_ids[0], target=names[0] if names else "",
                          place=u.place, text=u.text)
            return
        if u.verb == "warned":
            if listener_ids:
                self.on_warning(u.speaker, u.speaker_name, listener_ids[0], names[0] if names else listener_ids[0])
            return
        if listener_ids and self.lineage.is_player_rooted(u.id):
            if len(listener_ids) == 1:
                line = f'{u.speaker_name} told {names[0] if names else listener_ids[0]}: "{u.text}"'
            else:
                line = f'{u.speaker_name} said at the {u.place} (heard by {len(listener_ids)}): "{u.text}"'
            self.emit(
                "hop", line, uid=u.id, parent=u.parent, root=self.lineage.root(u.id),
                depth=self.lineage.depth(u.id), speaker_id=u.speaker, speaker=u.speaker_name,
                listeners=names, heard=len(listener_ids), place=u.place, verb=u.verb, text=u.text,
            )
        elif u.verb == "announced":
            self.emit("announcement", speaker=u.speaker_name, place=u.place, text=u.text)
