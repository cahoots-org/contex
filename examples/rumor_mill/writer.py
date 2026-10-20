"""Haiku writes the words; it never decides what anyone does."""

import json

from examples.rumor_mill.actions import STYLES
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.models import Villager

SAY_MAX_TOKENS = 80
TURN_AWAY_MAX_TOKENS = 60


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
    ) -> str:
        """The villager's words for this decision, in their voice and chosen style."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. Your grudge: {villager.grudge}. "
            "Reply with one or two short sentences, under 30 words, in your own voice, the way people gossip. "
            "Keep to what you heard, changed only as your manner says; bring up your grudge "
            "only when it fits. If what you heard is about you, answer as yourself. "
            "No stage directions, no quotation marks around the reply, no preamble."
        )
        lines = [f"Action: {decision.action}.", f"Listening: {audience}."]
        if decision.style:
            lines.append(f"Manner: {STYLES[decision.style]}")
        if source_text:
            lines.append(f"What you heard: {source_text}")
        return await self.complete(system, "\n".join(lines), max_tokens=SAY_MAX_TOKENS)

    async def brush_off(self, villager: Villager, concern: str) -> str:
        """The villager waving off the newcomer while grumbling about their concern, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "A newcomer just whispered something to you that you don't care about. Brush them off "
            "in one short sentence, under 25 words, in your own voice, grumbling about what is "
            "really on your mind and nothing else. "
            "Allude to it rather than state it plainly, so the listener has to guess the real "
            "problem: grumble about a symptom or a related worry, and don't name the specific "
            "thing, number, or person it is about. Never repeat its wording. "
            "No stage directions, no quotation marks, no preamble."
        )
        text = await self.complete(system, f"What is really on your mind: {concern}", max_tokens=SAY_MAX_TOKENS)
        return text.strip().split("\n")[0].strip().strip('"\u201c\u201d').strip()

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
        text = await self.complete(system, "The newcomer is whispering to you again.", max_tokens=TURN_AWAY_MAX_TOKENS)
        return text.strip().split("\n")[0].strip().strip('"\u201c\u201d').strip()

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
