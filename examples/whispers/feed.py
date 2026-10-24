"""The feed: typed events about the game, sent to every sink and kept for replay."""

import itertools
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from examples.whispers.game import Summary
from examples.whispers.goals import Goal, Trophy, claim, goal_targets
from examples.whispers.lineage import Lineage
from examples.whispers.models import Utterance
from examples.whispers.scenes import Scene
from examples.whispers.suspicion import HEAT_LINES
from examples.whispers.town import NEWCOMER, Town

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
MOMENTS = {
    "size": "{n} people have heard your rumor now.",
    "influence": "Your rumor reached {name}, and {influence}.",
    "place": "Your rumor has reached the {place}.",
    "scene": "Your rumor started {kind} at the {place}.",
    "turned": "Your rumor turned against you: {name} says a newcomer started it.",
}
GAME_OVER = {"lost": "Game over: you were run out of town."}
RESTING = "The village is resting for today. Come back after midnight UTC."
WAKING = "The village wakes for a new day."


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
        # Each closed player's id, with the id of the last event they see.
        self._closed: dict[str, int] = {}
        self._listening: dict[str, tuple[str, str]] = {}
        # ponytail: grows by one id per whisper the player sends; a session sends far fewer than would matter.
        self._resolved: set[str] = set()
        self._answered: set[str] = set()

    def emit(self, kind: str, line: str = "", /, player: str | None = None, **data) -> Event | None:
        """Send an event to every sink; drop one addressed to a closed player and return None."""
        if player in self._closed:
            return None
        event = Event(next(self._ids), time.time(), kind, line, data, player)
        self.recent.append(event)
        for sink in self.sinks:
            sink(event)
        return event

    def say(self, line: str, player: str | None = None) -> Event | None:
        """Send a plain line of text."""
        return self.emit("system", line, player=player, text=line)

    def close(self, player: str) -> None:
        """Send that player nothing newer than what has been sent so far."""
        self._closed[player] = self.recent[-1].id if self.recent else 0

    def sees(self, player: str, event: Event) -> bool:
        """Whether the event is meant for that player and came before their feed closed."""
        closed_at = self._closed.get(player)
        if closed_at is not None and event.id > closed_at:
            return False
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

    def on_progress(
        self, goal: Goal, fraction: float, counts: tuple[int, int] | None = None, player: str | None = None,
        rumor: str | None = None, doubt: dict | None = None, standing: str | None = None,
    ) -> None:
        """Progress on a player's goal, or on the world goal when player is None; standing words an action goal's.

        rumor, the player's whisper that moved it, goes out only to that player; doubt is how many heard the claim
        but don't believe it, and what the closest of them is saying.
        """
        percent = int(fraction * 100)
        believers, needed = counts or (None, None)
        shown = standing or (f"{believers} of {needed} believe it" if counts else f"{percent}%")
        self.emit("goal", f'Goal "{goal.text}": {shown}', player=player, goal_id=goal.id, text=goal.text, percent=percent,
                  met=False, believers=believers, needed=needed, world=player is None, doubt=doubt, standing=standing,
                  **({} if player is None else {"rumor": rumor}))

    def on_met(self, goal: Goal, player: str | None = None) -> None:
        """A player's goal met, or the world goal when player is None."""
        line = f"The whole village now holds that {claim(goal)}." if player is None else f'Goal met: "{goal.text}"'
        self.emit("goal", line, player=player, goal_id=goal.id, text=goal.text, percent=100, met=True,
                  world=player is None, line=line)

    def on_deal(self, goals: list[Goal], player: str, counts: dict[str, tuple[int, int]] | None = None) -> None:
        """A new hand of goals for the player, each belief goal with its believers and how many it needs."""
        hand = []
        for g in goals:
            believers, needed = (counts or {}).get(g.id, (None, None))
            hand.append({"id": g.id, "text": g.text, "believers": believers, "needed": needed, **goal_targets(g)})
        self.emit("deal", "\n".join(f"Goal: {g.text}" for g in goals), player=player, goals=hand)

    def on_goal_void(
        self, old: Goal, new: Goal, reason: str, player: str, counts: tuple[int, int] | None = None,
    ) -> None:
        """A goal that can no longer be met, swapped for a new one, told to its player alone."""
        believers, needed = counts or (None, None)
        text = f'"{old.text}" is off: {reason}. New goal: "{new.text}"'
        self.emit("goal_void", text, player=player, goal_id=old.id, text=text,
                  new={"id": new.id, "text": new.text, "believers": believers, "needed": needed, **goal_targets(new)})

    def on_goal_swapped(self, old: Goal, new: Goal, player: str, counts: tuple[int, int] | None = None) -> None:
        """A goal the player swapped for a new one, told to that player alone."""
        believers, needed = counts or (None, None)
        text = f'New goal: "{new.text}"'
        self.emit("goal_swapped", text, player=player, goal_id=old.id, text=text,
                  new={"id": new.id, "text": new.text, "believers": believers, "needed": needed, **goal_targets(new)})

    def on_world(self, goal: Goal, counts: tuple[int, int] | None = None) -> None:
        """A new world goal, for everyone, with its believers and how many it needs."""
        believers, needed = counts or (None, None)
        self.emit("world_goal", f'World goal: "{goal.text}"', goal_id=goal.id, text=goal.text, believers=believers,
                  needed=needed)

    def on_trophy(self, trophy: Trophy, line: str, player: str) -> None:
        """A trophy won by the player."""
        self.emit("trophy", line, player=player, trophy=trophy.to_json(), text=line)

    def on_heat(self, label: str, player: str | None = None) -> None:
        self.emit("heat", HEAT_LINES[label], player=player, label=label)

    def on_game_over(self, summary: Summary, player: str | None = None) -> None:
        lines = [GAME_OVER[summary.outcome], *(f"{text}: {percent}%" for text, percent in summary.goals), *summary.reasons]
        self.emit(
            "gameover", "\n".join(lines), player=player, outcome=summary.outcome,
            goals=[[text, percent] for text, percent in summary.goals], reasons=list(summary.reasons),
        )

    def on_whisper_status(self, whisper_id: str, villager_id: str, name: str, state: str, **extra) -> None:
        """Report where a whisper stands: checking, busy (with until), heard, brushed_off or near_miss (then with hint),
        not_carried, or what the villager did with it: kept, took_the_bait (with told), warned, answered
        (not final), or moved_on once a conversation ends.

        A whisper already resolved only takes another outcome: a late heard, brush-off, near miss or not-carried is
        dropped. An answered whisper drops a late listening state, brush-off or near miss.
        The terminal prints only a brush-off or near miss once its hint is settled. A miss also carries continues,
        whether the villager's next whisper continues the exchange.
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

    def on_departure(self, villager_id: str, name: str, farewell: str | None) -> None:
        """A villager left town for good, told to everyone, with their farewell if any."""
        text = f'{name} has left town for good: "{farewell}"' if farewell else f"{name} has left town for good."
        self.emit("departure", text, villager_id=villager_id, name=name, farewell=farewell, text=text)

    def on_arrival(self, villager_id: str, name: str, occupation: str) -> None:
        """A newcomer arrived at the gate, told to everyone."""
        text = f"{name}, a {occupation}, has arrived at the gate."
        self.emit("arrival", text, villager_id=villager_id, name=name, text=text)

    def on_storm_off(
        self, villager_id: str, name: str, line: str | None = None, *, player: str, verb: str = "storms off",
    ) -> None:
        """The villager broke off with the player, as verb says, with their parting words if any."""
        text = f'{name} {verb}: "{line}"' if line else f"{name} {verb}."
        self.emit("stormed_off", text, player=player, villager_id=villager_id, name=name, line=line, verb=verb)

    def on_denied(self, name: str, line: str | None, player: str) -> None:
        """The rumor's subject denied it aloud, in their own words."""
        self.emit("denied", f'{name}: "{line}"', player=player, name=name, line=line)

    def on_answer(self, villager_id: str, name: str, topic: str, line: str, *, player: str) -> None:
        """The villager's answer to the player asking what folk say about the topic."""
        self.emit("asked", f'{name}, asked about {topic}: "{line}"', player=player,
                  villager_id=villager_id, name=name, topic=topic, line=line)

    def on_warning(self, speaker_id: str, speaker_name: str, target_id: str, target_name: str, player: str) -> None:
        """A villager warned another about player."""
        self.emit(
            "warning", f"{speaker_name} warned {target_name} about you.", player=player,
            speaker_id=speaker_id, speaker=speaker_name, target_id=target_id, target=target_name,
        )

    def on_reply(self, conversation: str, utterance: Utterance) -> None:
        """A villager answered the newcomer in the conversation started by that whisper."""
        u = utterance
        self.emit("reply", f'{u.speaker_name} answers you: "{u.text}"', player=self.lineage.player_of(conversation),
                  conversation=conversation, villager_id=u.speaker, name=u.speaker_name, text=u.text, uid=u.id)

    def on_moment(self, rumor: str, line: str, tone: str = "good") -> None:
        """A turning point for the rumor, shown only to its player; dropped when the rumor has none."""
        player = self.lineage.player_of(rumor)
        if player is None:
            return
        self.emit("moment", line, player=player, rumor=rumor, text=line, tone=tone)

    def on_drift(self, whisper_id: str) -> None:
        """The whisper's story turned to other things; the terminal prints nothing."""
        self.emit("drift", player=self.lineage.player_of(whisper_id), whisper_id=whisper_id)

    def on_time(self, day: int, phase: str) -> None:
        line = TIME_LINES[phase].format(day=day)
        self.emit("time", line, text=line, day=day, phase=phase)

    def on_rest(self, resting: bool) -> None:
        """The village has started resting for the day, or woken."""
        line = RESTING if resting else WAKING
        self.emit("rest", line, text=line, resting=resting)

    def on_place(self, place: str, player: str | None = None) -> None:
        """The player is now at place; the terminal prints nothing."""
        self.emit("place", player=player, place=place)

    def on_listen(self, place: str, lines: list[Utterance], until: float, *, player: str) -> None:
        """The player starts listening at place until then, hearing first the lines said there lately."""
        heard = [f'{u.speaker_name}: "{u.text}"' for u in lines]
        text = "\n".join([f"You listen at the {place}.", *heard]) if heard else f"You listen at the {place}. It has been quiet here."
        self.emit("listen", text, player=player, place=place, until=until, ended=False,
                  lines=[{"speaker": u.speaker, "speaker_name": u.speaker_name, "text": u.text} for u in lines])

    def on_listen_end(self, place: str, *, player: str) -> None:
        """The player stops listening at place."""
        self.emit("listen", "You stop listening.", player=player, place=place, ended=True)

    def on_overheard(
        self, utterance: Utterance, reason: str, scene: str | None = None, player: str | None = None,
    ) -> None:
        """A line the player overheard at their place, and the scene it belongs to if any.

        The terminal prints only a line heard while listening.
        """
        u = utterance
        line = f'{u.speaker_name}: "{u.text}"' if reason == "listen" else ""
        self.emit("overheard", line, player=player, speaker=u.speaker, speaker_name=u.speaker_name, place=u.place, text=u.text,
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
            if listener_ids and u.about is not None:
                self.on_warning(
                    u.speaker, u.speaker_name, listener_ids[0], names[0] if names else listener_ids[0], u.about,
                )
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
