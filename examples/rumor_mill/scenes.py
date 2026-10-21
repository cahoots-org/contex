"""Scenes: conversations that keep going while their momentum holds."""

import random
import time
from dataclasses import dataclass, field

from examples.rumor_mill.actions import CONFRONT, DENY, RETELL, SPEAK, WARN
from examples.rumor_mill.config import Config
from examples.rumor_mill.models import Utterance
from examples.rumor_mill.overhear import HEATED_ACTIONS, HEATED_STYLES, LIVELY_STYLES
from examples.rumor_mill.town import PLAYER_ID, Town

# Speech that keeps a scene going.
SCENE_SPEECH = (RETELL, DENY, CONFRONT, WARN, SPEAK)
MAX_PEOPLE = 6
# A scene ends when its momentum falls below this.
FLOOR = 0.25
# What each turn costs the scene's momentum.
DECAY = 0.4
# What a villager leaving costs on top of DECAY.
LEAVE_DROP = 0.2
JOIN_BONUS = 0.1
OPENING_HEAT = {"heated": 0.6, "lively": 0.45, "plain": 0.3}
STYLE_BONUS = {"heated": 0.15, "lively": 0.05, "plain": 0.0}
# A scene with no new line for this many scene cooldowns has cooled.
STALL_COOLDOWNS = 4


def heat(action: str | None, style: str | None) -> str:
    """How heated a line is: heated, lively, or plain."""
    if action in HEATED_ACTIONS or style in HEATED_STYLES:
        return "heated"
    if style in LIVELY_STYLES:
        return "lively"
    return "plain"


@dataclass
class Scene:
    id: str
    place: str
    # The card uid that lines are checked against to stay on topic.
    topic: str
    # The whisper a player-rooted scene grew from.
    rumor: str | None
    heated: bool
    opening: str
    participants: set[str]
    # Everyone who has spoken in the scene.
    people: set[str]
    lines: list[str]
    momentum: float
    # Why it ended: veered, cooled, long, or alone.
    ended: str | None = None
    # When its last line was said, on the monotonic clock.
    last_line: float = field(default_factory=time.monotonic)


@dataclass
class Scenes:
    config: Config
    town: Town
    rng: random.Random
    _active: dict[str, Scene] = field(default_factory=dict)
    _count: int = 0

    def of(self, vid: str) -> Scene | None:
        """The scene vid is taking part in."""
        return next((s for s in self._active.values() if vid in s.participants), None)

    def holding(self, uid: str) -> Scene | None:
        """The scene that has the line uid."""
        return next((s for s in self._active.values() if uid in s.lines), None)

    def full(self) -> bool:
        """Whether the village is running as many scenes as it will."""
        return len(self._active) >= self.config.max_scenes

    def start(self, opening: Utterance, answerer: str, topic: str, rumor: str | None) -> Scene | None:
        """Begin a scene from an opening line, or None when the village is running as many as it will."""
        if self.full():
            return None
        self._count += 1
        kind = heat(opening.action, opening.style)
        scene = Scene(
            f"s-{self._count}", opening.place, topic, rumor, kind == "heated", opening.id,
            {opening.speaker, answerer}, {opening.speaker}, [opening.id], OPENING_HEAT[kind],
        )
        self._active[scene.id] = scene
        return scene

    def join(self, scene: Scene, vid: str) -> bool:
        """Add vid to the scene; False when it is full."""
        if len(scene.participants) >= MAX_PEOPLE:
            return False
        scene.participants.add(vid)
        return True

    def speaking(self, u: Utterance) -> Scene | None:
        """The scene u continues: scene speech from a participant at the scene's place."""
        scene = self.of(u.speaker)
        if scene and u.place == scene.place and u.action in SCENE_SPEECH:
            return scene
        return None

    def said(self, scene: Scene, uid: str) -> None:
        scene.lines.append(uid)
        scene.last_line = time.monotonic()

    def stalled(self) -> list[Scene]:
        """The scenes with no new line for STALL_COOLDOWNS scene cooldowns."""
        cutoff = time.monotonic() - STALL_COOLDOWNS * self.config.scene_cooldown_seconds
        return [s for s in self._active.values() if s.last_line < cutoff]

    def addressed(self, scene: Scene, speaker: str) -> list[str]:
        """Who hears the speaker: the other participants here, then bystanders drawn by momentum."""
        here = [v.id for v in self.town.present(scene.place, exclude=speaker)]
        heard = [vid for vid in here if vid in scene.participants]
        room = MAX_PEOPLE - len(scene.participants)
        chance = scene.momentum * self.config.join_rate
        for vid in here:
            if vid in scene.participants or vid == PLAYER_ID or room <= 0:
                continue
            if self.rng.random() < chance:
                heard.append(vid)
                room -= 1
        return heard

    def turned(
        self, scene: Scene, vid: str, action: str | None, style: str | None, keen: float | None, joined: bool,
    ) -> str | None:
        """Apply vid's turn to the scene's momentum and return why it ended, if it did.

        Anything but scene speech, including None for a failed turn or a departure, leaves the scene.
        """
        if action in SCENE_SPEECH:
            scene.momentum += (keen or 0) + STYLE_BONUS[heat(action, style)] + (JOIN_BONUS if joined else 0) - DECAY
            scene.people.add(vid)
        else:
            scene.participants.discard(vid)
            scene.momentum -= DECAY + LEAVE_DROP
        scene.momentum = min(1.0, max(0.0, scene.momentum))
        here = sum(1 for p in scene.participants if p in self.town and self.town.place_of(p) == scene.place)
        if here < 2:
            return "alone"
        if scene.momentum < FLOOR:
            return "cooled"
        if len(scene.lines) >= self.config.scene_max_lines:
            return "long"
        return None

    def end(self, scene: Scene, reason: str) -> None:
        scene.ended = reason
        self._active.pop(scene.id, None)
