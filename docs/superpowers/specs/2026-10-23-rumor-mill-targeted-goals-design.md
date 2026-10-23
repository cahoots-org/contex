# Rumor Mill: Targeted Goals and Not Tipping Your Hand — Design

**Status:** approved (Rob, 2026-10-23: "lgtm")

## Intent

Rob: "We need more specific goals. If they're all 'A third of the village believes X' it gets boring.
There should be goals like 'Convince X that Y is stealing their Z'. Can we dynamically generate them as
well?" Then: "There should definitely be an element of making sure you're not tipping your hand to the
people you're trying to manipulate."

Today every belief goal is a fixed deck entry counted over the whole village. Goals should be about
real people in this village. Playing them well should mean steering a rumor toward someone without
being caught doing it.

## Goals

- Each hand mixes three goal kinds: personal, circle and village-wide.
- Goals are written for this village's real people, so no two hands read alike.
- Telling the mark directly is a gamble. Getting the claim to them secondhand is the skill.
- The person a rumor is about can trace it back to you and fight back.
- How hard a goal is matches how believable its claim is. This takes in the approved
  "believability-based goal bars" task.

## Non-goals

- Fight-back beyond a denial. No confrontation scenes and no subject leaving town over it.
- State goals ("N villagers leave town") are unchanged.
- The world goal stays village-wide and taken from the fixed deck.

## Goal kinds

A goal gains an **audience** (the villager ids that count; empty means the whole village) and an
optional **subject** (the villager the claim is about).

| Kind | Example | Audience | Bar |
|---|---|---|---|
| Personal | "Convince Agnes that Hob is stealing her geese" | one villager (the mark) | 1 |
| Circle | "Get the farmers to believe the miller waters his flour" | an occupation, a family (shared surname) or the people tied to one villager in `relationships`, 3 or more members | from believability, of the audience |
| Village-wide | "A third of the village believes the well is cursed" | everyone | from believability |

A hand is one of each. Only the village-wide slot may be a state goal instead, so a hand always
keeps its personal and circle goals. *(My call.)*

The village-wide goal and the world goal come from the fixed deck. Haiku writes only the personal
and circle goals. *(My call: deck claims are vetted, and they are the ones everyone shares.)*

Every generated goal carries its **owner** (the player id). The judge uses it for the per-player
rules below. Deck goals have no owner, marks or subject.

**When audience members leave.** If a personal goal's mark or subject leaves town, or a circle
falls below 3 members in town, the goal is void. It is replaced by a goal from the deck, and the
player is told why in one quiet feed line. *(My call.)*

## Generating goals

- Goals are written when they are dealt, per player, by one Haiku call. The call gets a sample of
  the town: names, occupations, grudges and relationships, about 15 villagers who are in town now.
  It returns, per goal: the text, the claim (the `evidence` line the judge queries Contex with),
  the believer question, the audience spec and the subject id.
- Code validates every goal and rejects any that fail these checks:
  - every named villager is in town;
  - the mark is not the subject;
  - a circle resolves to 3 or more villagers in town;
  - the claim names the subject when there is a subject;
  - the claim does not repeat a goal another player holds now or the current world goal.
- Each rejected goal is replaced from the fixed deck. If the call itself fails, the whole hand
  comes from the deck. A hand is never delayed waiting for Haiku past a short timeout.
- Generated goal ids are unique per deal (for example `gen-<player>-<n>`). Today's code keys
  progress, counts, roots and doubt by goal id, so ids must never collide between players.
- Dealing runs as a background task so it never stalls the judge loop. Until the new hand
  arrives, the player keeps the hand they just finished, shown as met. A player's very first hand
  comes from the deck, so a new player never waits on Haiku. *(My call.)*

## Believability sets the bar

- When a goal is generated, Jev scores its claim once for how believable it is to an ordinary
  villager, and the result is cached per claim. Fixed deck claims are scored once at startup.
- The bar is the fraction of the audience that must believe. Easy claims need about half; the
  most far-fetched need about a fifth. Personal goals always need their one mark.
- A goal whose claim scores in the bottom band is labelled **Long shot**. It shows a distinct
  mark on its goal card and its trophy. This replaces the hand-set fractions in the deck.

## Not tipping your hand

**The mark.** A mark is a personal goal's mark or a circle member.
- When you whisper to a mark, Jev answers one question about it. The state is the mark, the
  goal's claim and subject, and your whisper. The question is whether the newcomer is plainly
  trying to turn this villager against the subject. The check runs after delivery as a background
  task, charged to the player, so it never slows a whisper. *(My call.)*
- A yes adds a full whisper's suspicion bump for that player on top of the usual one, with the
  reason "worked on them". *(My call.)*
- A mark whose suspicion of you is at or above `warn_threshold` does not count as a believer for
  your goals. They believe it, but not on your word.
- Village-wide goals have no marks.

**The subject.**
- A subject grows suspicious of you when they hear the claim through a chain that starts with
  your whisper. On each check of the goal, the judge queries the subject's mind for the claim, the
  same query it makes of the audience. A matching memory whose lineage roots in your whisper
  raises the subject's suspicion of you. This needs no extra model call, and unrelated rumors of
  yours that reach the subject don't count. *(My call.)* The bump is applied once per subject,
  player and rumor root, and its size scales with hops from your whisper:

  | Hops | Bump |
  |---|---|
  | 1 | a full whisper bump |
  | 2 | half |
  | 3 | a quarter |
  | 4 or more | none |

  The bump uses the reason "traced it to you".
- When a bump carries a subject's suspicion of you past `warn_threshold`, the subject denies the
  rumor to each mark, once per goal. The denial is one Haiku line in the subject's voice, published to the mark's mind
  like any line they hear, with no lineage to you. It then competes with your rumor in the
  judge's memory query.

**What the player sees.** While the goal is live, the mark and subject get small badges in the
village column, private to that player. The trophy line adds "and {subject} never knew" when the
subject's suspicion of you stayed under `warn_threshold`.

## Multiplayer

All of this is per player, like suspicion. One player's subject tracing a rumor raises suspicion
only of the player whose whisper it roots in. Two players may get goals about the same villager.
Validation only forbids the same claim.

## Left to the builder

- The Haiku prompt and output format, the believability Score levels and band edges, badge
  styling, the denial prompt, and how the sample of 15 is chosen. Prefer villagers with
  relationships and grudges.
