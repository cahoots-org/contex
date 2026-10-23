"""Haiku writes the words; it never decides what anyone does."""

import json
import re
from collections.abc import Sequence

from examples.rumor_mill.actions import STYLES
from examples.rumor_mill.decide import Decision
from examples.rumor_mill.models import Villager
from examples.rumor_mill.usage import charged_to

SAY_MAX_TOKENS = 80
TURN_AWAY_MAX_TOKENS = 60
WARN_MAX_TOKENS = 60
GO_ON_MAX_TOKENS = 40
GOALS_MAX_TOKENS = 600
POOL_MAX_TOKENS = 2500
# How a player whisper reads when it reaches a villager.
WHISPER_FRAMING = re.compile(r"^A newcomer whispered to you: ")


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


def _sketch(villager: Villager, names: dict[str, str]) -> str:
    """One line on the villager for the goal writer: who they are, their grudge, and their ties among names."""
    line = f"- {villager.name}, {villager.occupation}, {villager.personality}."
    if villager.grudge:
        line += f" Grudge: {villager.grudge}."
    ties = [f"{names[vid]} ({kind})" for vid, kind in villager.relationships.items() if vid in names]
    if ties:
        line += f" Ties: {', '.join(ties)}."
    return line


GOALS_SYSTEM = (
    "You write secret goals for a player in a village gossip game. The player is a newcomer who spreads "
    "rumors to turn villagers against one another. Write two goals about the villagers listed: petty, "
    "specific village intrigue that grows out of their grudges and ties, such as \"Convince Agnes that Hob "
    "is stealing her geese\" or \"Get the farmers to believe the miller waters his flour\".\n"
    "Reply with only a JSON array of two objects:\n"
    '[{"kind": "personal", "claim": "...", "question": "...", '
    '"mark": "<full name>", "subject": "<full name>"}, '
    '{"kind": "circle", "claim": "...", "question": "...", "label": "...", '
    '"members": ["<full name>", ...], "subject": "<full name>"}]\n'
    "Rules:\n"
    "- The personal goal is to convince one villager, the mark, of a claim about another, the subject. "
    "The player is told \"Convince <mark's first name> that <claim>\".\n"
    "- The circle goal is to turn a group of three to five listed villagers against the subject: people "
    "who share a trade, a family, a haunt or a cause. members are their full names. label is how the group "
    "reads in a sentence, such as \"the tradesfolk\", \"the chapel faithful\" or \"Agnes's friends\". The "
    "subject is not one of the group.\n"
    "- The claim is the rumor as a plain statement that names the subject by first name, such as "
    "\"Hob is stealing Agnes's geese\".\n"
    "- The question is a yes or no question asked about one villager, such as \"Does this villager "
    "believe Hob is stealing Agnes's geese?\"\n"
    "- Use full names exactly as listed. The mark and the subject are different people.\n"
    "- Write in the third person. Never use \"you\" or \"your\".\n"
    "- Never repeat a claim already in play."
)


POOL_SYSTEM = (
    "You write the rumors for a village gossip game. Given the villagers, reply with only a JSON object "
    '{"accusations": ["..."], "claims": ["..."]}.\n'
    "- About 40 accusations. Each completes the sentence \"{someone} ...\", such as \"is stealing from the "
    "tithe box\" or \"waters down the ale\": petty, specific, and fitted to the trades and grudges listed, "
    "though never to one villager in particular.\n"
    "- About 15 claims. Each is a whole statement about the village, such as \"the well is cursed\".\n"
    "Rules:\n"
    "- Under 80 characters each.\n"
    "- Third person. Never use \"you\" or \"your\".\n"
    "- Name no one and no place by name."
)


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
            self.usage.record("haiku", response.usage.input_tokens, response.usage.output_tokens, charged_to.get())
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
            "You may name the subject itself, such as strange lights or the vines, but never a "
            "name, a number, or any particular. Never repeat its wording. "
            "No stage directions, no quotation marks, no preamble."
        )
        return first_line(await self.complete(system, f"What is really on your mind: {concern}", max_tokens=SAY_MAX_TOKENS))

    async def go_on(self, villager: Villager, newcomer_line: str) -> str:
        """The villager leaning in to urge the newcomer on, in one line, without saying what interests them."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "A newcomer just whispered something that nearly caught your interest. Urge them to go on "
            "in one short sentence, under 15 words, in your own voice. "
            "Never say what interests you or what you want to hear. "
            "No stage directions, no quotation marks, no preamble."
        )
        return first_line(await self.complete(system, f"The newcomer said: {newcomer_line}", max_tokens=GO_ON_MAX_TOKENS))

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

    async def storm_off(self, villager: Villager) -> str:
        """The villager breaking off with the newcomer who has tried their patience, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "The newcomer you were talking with has tried your patience, and you storm off. Say your parting "
            "words in one short sentence, about 15 words, in your own voice. "
            "Never mention suspicion. "
            "No stage directions, no quotation marks, no preamble."
        )
        return first_line(
            await self.complete(system, "You walk away from the newcomer.", max_tokens=TURN_AWAY_MAX_TOKENS)
        )

    async def deny(self, villager: Villager, claim: str) -> str:
        """The villager flatly denying the claim about them, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "A rumor is going around about you and you deny it flatly. Say so in one short sentence, under 20 words, "
            "in your own voice. "
            "Never name or describe the newcomer or anyone who might be behind it. "
            "No stage directions, no quotation marks, no preamble."
        )
        user = f"The rumor: {claim}"
        return first_line(await self.complete(system, user, max_tokens=SAY_MAX_TOKENS))

    async def warn(self, villager: Villager, target: Villager) -> str:
        """The villager warning the target that the newcomer is behind the rumors, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "You have come to believe a newcomer in town is behind the rumors going around. "
            "Warn the person you are talking to in one short sentence, under 25 words, in your own voice. "
            "Never say how sure you are or how many people suspect the newcomer. "
            "No stage directions, no quotation marks, no preamble."
        )
        user = f"You are talking to {target.name}, a {target.occupation}."
        return first_line(await self.complete(system, user, max_tokens=WARN_MAX_TOKENS))

    async def reply(self, villager: Villager, newcomer_line: str, said: list[str], heard: Sequence[str] = ()) -> str:
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
        if heard:
            system += " If you already heard about this, say so and what you heard, in your own words."
            lines.append("What you heard earlier: " + " / ".join(heard))
        return first_line(await self.complete(system, "\n".join(lines), max_tokens=SAY_MAX_TOKENS))

    async def answer(self, villager: Villager, topic: str, heard: Sequence[str]) -> str:
        """The villager telling the newcomer what folk have said about the topic, in one line."""
        system = (
            f"You are {villager.name}, a {villager.occupation} in a small village. "
            f"Your personality: {villager.personality}. "
            "The newcomer asks what folk have been saying about something. Answer in one short sentence, "
            "under 30 words, in your own voice, naming who told you and where. If you heard nothing about it, "
            "say so. Never mention suspicion. No stage directions, no quotation marks, no preamble."
        )
        said = "What you heard: " + " / ".join(heard) if heard else "What you heard: nothing about it."
        user = f"The newcomer asks about: {topic}\n{said}"
        return first_line(await self.complete(system, user, max_tokens=SAY_MAX_TOKENS))

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

    async def goals(self, sample: list[Villager], taken: list[str]) -> list[dict]:
        """A personal and a circle goal about the sampled villagers, as Haiku wrote them, or [] if unreadable."""
        names = {v.id: v.name for v in sample}
        user = "Villagers:\n" + "\n".join(_sketch(v, names) for v in sample)
        if taken:
            user += "\nClaims already in play:\n" + "\n".join(f"- {t}" for t in taken)
        try:
            parsed = extract_json(await self.complete(GOALS_SYSTEM, user, max_tokens=GOALS_MAX_TOKENS))
        except ValueError:
            return []
        return [g for g in parsed if isinstance(g, dict)] if isinstance(parsed, list) else []

    async def goal_pool(self, villagers: list[Villager]) -> dict:
        """Accusations and village-wide claims fitted to the villagers, as Haiku wrote them, or {} if unreadable."""
        user = "Villagers:\n" + "\n".join(
            f"- {v.occupation}, {v.personality}." + (f" Grudge: {v.grudge}." if v.grudge else "") for v in villagers
        )
        try:
            parsed = extract_json(await self.complete(POOL_SYSTEM, user, max_tokens=POOL_MAX_TOKENS))
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
