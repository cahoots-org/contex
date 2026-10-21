"""Haiku writes the words; it never decides what anyone does."""

import json
import re

from examples.rumor_mill.actions import STYLES
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.models import Villager

SAY_MAX_TOKENS = 80
TURN_AWAY_MAX_TOKENS = 60
WARN_MAX_TOKENS = 60
# How a player whisper reads when it reaches a villager.
WHISPER_FRAMING = re.compile(r"^The newcomer whispered to you at the [^:]*: ")


STAGE_DIRECTION = re.compile(r"\*[^*]*\*")


def first_line(text: str) -> str:
    """The first line of a reply, without stage directions or surrounding quotes."""
    line = text.strip().split("\n")[0]
    line = re.sub(r"\s{2,}", " ", STAGE_DIRECTION.sub("", line))
    return line.strip().strip('"\u201c\u201d').strip()


def extract_json(text: str):
    """Parse the first JSON array or object in text, ignoring fences and prose."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                return decoder.raw_decode(text, i)[0]
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON in model output: {text[:200]!r}")


def _grudge(villager: Villager, aside: str = "") -> str:
    """The villager's grudge for a prompt, then aside, or nothing when they hold none."""
    return f"Your grudge: {villager.grudge}. {aside}" if villager.grudge else ""


class Writer:
    def __init__(self, client, model: str, usage=None):
        self.client = client
        self.model = model
        self.usage = usage

    async def complete(self, system: str, user: str, max_tokens: int = 400) -> str:
        """One Haiku call, returning the reply text."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if self.usage:
            self.usage.record("haiku", response.usage.input_tokens, response.usage.output_tokens)
        return response.content[0].text.strip()

    async def say(
        self, villager: Villager, decision: Decision, source_text: str | None, audience: str,
        names_newcomer: bool = False,
    ) -> str:
        """The villager's words for this decision, in their voice and chosen style.

        Unless names_newcomer, the newcomer's whisper framing is removed and the villager keeps the source unnamed.
        """
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            + _grudge(villager, "Bring it up only when it fits. ")
            + "Reply with one or two short sentences, under 30 words, in your own voice, the way people gossip. "
            "You gossip with relish and curiosity, not malice, unless your manner says otherwise. "
            "Keep to what you heard, changed only as your manner says. "
            "If what you heard is about you, answer as yourself. "
            "No stage directions, no quotation marks around the reply, no preamble."
        )
        lines = [f"Action: {decision.action}.", f"Listening: {audience}."]
        if decision.style:
            lines.append(f"Manner: {STYLES[decision.style]}")
        if source_text and names_newcomer:
            lines.append(f"What you heard: {source_text}")
            lines.append("This story names the newcomer; keep that in.")
        elif source_text:
            lines.append(f"What you heard: {WHISPER_FRAMING.sub('', source_text)}")
            lines.append("Do not say or hint who started this story.")
        return first_line(await self.complete(system, "\n".join(lines), max_tokens=SAY_MAX_TOKENS))

    async def brush_off(self, villager: Villager, concern: str) -> str:
        """The villager waving off the newcomer in character, alluding to their concern, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "A newcomer just whispered something to you that you don't care about. Wave them off "
            "in one short sentence, under 25 words, in your own voice and in keeping with your "
            "temperament: a kindly villager is busy and apologetic, a sour one curt. Mention only "
            "what is really on your mind and nothing else. "
            "Allude to it rather than state it plainly, so the listener has to guess the real "
            "problem: mention a symptom or a related worry, and don't name the specific "
            "thing, number, or person it is about. Never repeat its wording. "
            "No stage directions, no quotation marks, no preamble."
        )
        return first_line(await self.complete(system, f"What is really on your mind: {concern}", max_tokens=SAY_MAX_TOKENS))

    async def turn_away(self, villager: Villager) -> str:
        """The villager refusing to hear the newcomer any more, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "You have grown wary of the newcomer who keeps whispering to you. Refuse to hear "
            "them out in one short sentence, about 15 words, in your own voice. "
            "Never mention suspicion or why. "
            "No stage directions, no quotation marks, no preamble."
        )
        return first_line(
            await self.complete(system, "The newcomer is whispering to you again.", max_tokens=TURN_AWAY_MAX_TOKENS)
        )

    async def warn(self, villager: Villager, target: Villager) -> str:
        """The villager warning the target that the newcomer is behind the rumors, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "You have come to believe the newcomer in town is behind the rumors going around. "
            "Warn the person you are talking to in one short sentence, under 25 words, in your own voice. "
            "Never say how sure you are or how many people suspect the newcomer. "
            "No stage directions, no quotation marks, no preamble."
        )
        user = f"You are talking to {target.name}, a {target.occupation}."
        return first_line(await self.complete(system, user, max_tokens=WARN_MAX_TOKENS))

    async def reply(self, villager: Villager, newcomer_line: str, said: list[str]) -> str:
        """The villager answering what the newcomer just said to them, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            + _grudge(villager)
            + "The newcomer in town just said something to you that you care about. Answer them "
            "in one short sentence, under 30 words, in your own voice, responding to what they said. "
            "Never mention suspicion. "
            "No stage directions, no quotation marks, no preamble."
        )
        lines = [f"The newcomer said: {WHISPER_FRAMING.sub('', newcomer_line)}"]
        if said:
            lines.append("What you said earlier: " + " / ".join(said))
        return first_line(await self.complete(system, "\n".join(lines), max_tokens=SAY_MAX_TOKENS))

    async def describe_group(
        self, villager: Villager, inspiration: list[str],
    ) -> tuple[str, str]:
        """A name and creed for a group the villager founds."""
        system = (
            f"You are {villager.name}, a {villager.occupation} ({villager.personality}). "
            "You are founding a village group. Reply with only a JSON object "
            '{"name": "...", "creed": "..."}: a short name and a one-sentence creed.'
        )
        user = "What inspired you:\n" + "\n".join(f"- {t}" for t in inspiration)
        parsed = extract_json(await self.complete(system, user))
        return parsed["name"], parsed["creed"]
