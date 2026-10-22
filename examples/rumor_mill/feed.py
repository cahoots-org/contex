"""The feed: typed events about the game, sent to every sink and kept for replay."""

import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from examples.rumor_mill.game import Summary
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.lineage import Lineage
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.scenes import Scene
from examples.rumor_mill.suspicion import HEAT_LINES
from examples.rumor_mill.town import NEWCOMER, Town

REPLAY_EVENTS = 500
LISTENING = ("checking", "busy", "heard")
RESOLVED = ("kept", "took_the_bait", "warned", "moved_on")
LEANING = ("brushed_off", "near_miss")
TIME_LINES = {
    "morning": "Day {day} dawns over the village.",
    "afternoon": "The sun is high over the village.",
    "evening": "Evening falls, and lamps are lit.",
    "night": "Night settles over the village.",
}
SCENE_ENDS = {
    "veered": "The {kind} turned to other things.",
    "cooled": "The {kind} died down.",
    "long": "The {kind} ran its course.",
    "alone": "The {kind} broke up.",
}
GAME_OVER = {"won": "You won: every goal is met.", "lost": "Game over: you were run out of town."}


@dataclass(frozen=True)
class Event:
    id: int
    at: float
    kind: str
    # What the terminal prints; empty when it prints nothing.
    line: str
    data: dict
    # Who sees the event; None means everyone in the village.
    player: str | None = None

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
        self.closed = False
        self._listening: dict[str, tuple[str, str]] = {}
        # ponytail: grows by one id per whisper the player sends; a session sends far fewer than would matter.
        self._resolved: set[str] = set()
        self._answered: set[str] = set()

    def emit(self, kind: str, line: str = "", /, player: str | None = None, **data) -> Event | None:
        """Send an event to every sink; once the feed is closed, drop it and return None."""
        if self.closed:
            return None
        event = Event(next(self._ids), time.time(), kind, line, data, player)
        self.recent.append(event)
        for sink in self.sinks:
            sink(event)
        return event

    def say(self, line: str, player: str | None = None) -> None:
        self.emit("system", line, player=player, text=line)

    def sees(self, player: str, event: Event) -> bool:
        return event.player is None or event.player == player

    def replay(self, player: str) -> list[Event]:
        """The recent events that player sees."""
        return [e for e in self.recent if self.sees(player, e)]

    def view(self, player: str, sink: Sink) -> Sink:
        """A sink passing on to sink only the events that player sees."""
        def seen(event: Event) -> None:
            if self.sees(player, event):
                sink(event)

        return seen

    def on_progress(self, goal: Goal, fraction: float, counts: tuple[int, int] | None = None) -> None:
        percent = int(fraction * 100)
        believers, needed = counts or (None, None)
        shown = f"{believers} of {needed} believe it" if counts else f"{percent}%"
        self.emit("goal", f'Goal "{goal.text}": {shown}', goal_id=goal.id, text=goal.text, percent=percent, met=False,
                  believers=believers, needed=needed)

    def on_met(self, goal: Goal) -> None:
        self.emit("goal", f'Goal met: "{goal.text}"', goal_id=goal.id, text=goal.text, percent=100, met=True)

    def on_heat(self, label: str) -> None:
        self.emit("heat", HEAT_LINES[label], label=label)

    def on_game_over(self, summary: Summary) -> None:
        lines = [GAME_OVER[summary.outcome], *(f"{text}: {percent}%" for text, percent in summary.goals), *summary.reasons]
        self.emit(
            "gameover", "\n".join(lines), outcome=summary.outcome,
            goals=[[text, percent] for text, percent in summary.goals], reasons=list(summary.reasons),
        )

    def on_whisper_status(self, whisper_id: str, villager_id: str, name: str, state: str, **extra) -> None:
        """Report where a whisper stands: checking, busy (with until), heard, brushed_off or near_miss (then with hint),
        not_carried, or what the villager did with it: kept, took_the_bait (with told), warned, answered
        (not final), or moved_on once a conversation ends.

        A whisper already resolved only takes another outcome: a late heard, brush-off, near miss or not-carried is
        dropped. An answered whisper drops a late listening state, brush-off or near miss.
        The terminal prints only a brush-off or near miss once its hint is settled.
        """
        if whisper_id in self._resolved and state not in RESOLVED:
            return
        if whisper_id in self._answered and (state in LISTENING or state in LEANING):
            return
        if state == "answered":
            self._answered.add(whisper_id)
        if state in LISTENING:
            self._listening[whisper_id] = (villager_id, name)
        else:
            self._listening.pop(whisper_id, None)
        if state in RESOLVED:
            self._resolved.add(whisper_id)
        text = ""
        if state in LEANING and "hint" in extra:
            hint = extra["hint"]
            if state == "near_miss":
                text = f'{name} leans in: "{hint}"' if hint else f"{name} leans in."
            else:
                text = f'{name} brushes you off: "{hint}"' if hint else f"{name} shrugs it off."
        self.emit("whisper_status", text, player=self.lineage.player_of(whisper_id), whisper_id=whisper_id,
                  villager_id=villager_id, name=name, state=state, **extra)

    def listening(self, villager_id: str) -> list[tuple[str, str]]:
        """The (whisper id, name) of each whisper the villager is still hearing."""
        return [(wid, name) for wid, (vid, name) in self._listening.items() if vid == villager_id]

    def on_refusal(self, villager_id: str, name: str, line: str | None = None, player: str | None = None) -> None:
        text = f'{name} turns away from you: "{line}"' if line else f"{name} turns away from you."
        self.emit("refusal", text, player=player, villager_id=villager_id, name=name, line=line)

    def on_warning(self, speaker_id: str, speaker_name: str, target_id: str, target_name: str) -> None:
        self.emit(
            "warning", f"{speaker_name} warned {target_name} about you.",
            speaker_id=speaker_id, speaker=speaker_name, target_id=target_id, target=target_name,
        )

    def on_reply(self, conversation: str, utterance: Utterance) -> None:
        """A villager answered the newcomer in the conversation started by that whisper."""
        u = utterance
        self.emit("reply", f'{u.speaker_name} answers you: "{u.text}"', player=self.lineage.player_of(conversation),
                  conversation=conversation, villager_id=u.speaker, name=u.speaker_name, text=u.text, uid=u.id)

    def on_drift(self, whisper_id: str) -> None:
        """The whisper's story turned to other things; the terminal prints nothing."""
        self.emit("drift", player=self.lineage.player_of(whisper_id), whisper_id=whisper_id)

    def on_time(self, day: int, phase: str) -> None:
        line = TIME_LINES[phase].format(day=day)
        self.emit("time", line, text=line, day=day, phase=phase)

    def on_place(self, place: str, player: str | None = None) -> None:
        """The player is now at place; the terminal prints nothing."""
        self.emit("place", player=player, place=place)

    def on_overheard(
        self, utterance: Utterance, reason: str, scene: str | None = None, player: str | None = None,
    ) -> None:
        """A line the player overheard at their place, and the scene it belongs to if any; the terminal prints nothing."""
        u = utterance
        self.emit("overheard", player=player, speaker=u.speaker, speaker_name=u.speaker_name, place=u.place, text=u.text,
                  reason=reason, uid=u.id, scene=scene)

    def on_scene(self, scene: Scene, player: str | None = None) -> None:
        """A scene with at least two people went on or ended; the terminal prints nothing."""
        if len(scene.people) < 2:
            return
        tone = "argument" if scene.heated else "conversation"
        self.emit(
            "scene", player=player, scene_id=scene.id, rumor=scene.rumor, opening=scene.opening, place=scene.place, tone=tone,
            people=len(scene.people), lines=len(scene.lines),
            ended=SCENE_ENDS[scene.ended].format(kind=tone) if scene.ended else None,
        )

    def on_delivery(
        self, utterance: Utterance, listener_ids: list[str], on_topic: bool = True, scene: str | None = None,
    ) -> None:
        """Emit the player's whisper, a warning, an on-topic hop of a player-rooted rumor, or a town announcement.

        scene is the id of the scene a hop belongs to, if any.
        """
        u = utterance
        names = [self.town.get(i).name for i in listener_ids if i in self.town]
        if u.speaker == NEWCOMER:
            if listener_ids:
                self.emit("whisper", player=self.lineage.player_of(u.id), uid=u.id, target_id=listener_ids[0], target=names[0] if names else "",
                          place=u.place, text=u.text, conversation=u.conversation)
            return
        if u.verb == "warned":
            if listener_ids:
                self.on_warning(u.speaker, u.speaker_name, listener_ids[0], names[0] if names else listener_ids[0])
            return
        player = self.lineage.player_of(u.id)
        if listener_ids and player is not None:
            if not on_topic:
                return
            if len(listener_ids) == 1:
                line = f'{u.speaker_name} told {names[0] if names else listener_ids[0]}: "{u.text}"'
            else:
                line = f'{u.speaker_name} said at the {u.place} (heard by {len(listener_ids)}): "{u.text}"'
            self.emit(
                "hop", line, player=player, uid=u.id, parent=u.parent, root=self.lineage.root(u.id),
                depth=self.lineage.depth(u.id), speaker_id=u.speaker, speaker=u.speaker_name,
                listeners=names, heard=len(listener_ids), place=u.place, verb=u.verb, text=u.text, scene=scene,
            )
        elif u.verb == "announced":
            self.emit("announcement", speaker=u.speaker_name, place=u.place, text=u.text)
