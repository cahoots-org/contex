# Rumor Mill — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-19

## Intent

A reference app that people evaluating Contex clone, run, and keep playing.
The coding-agent and ops demos don't need Contex: their data is one tidy
source and grep works. Rumor Mill can't exist without it, because the whole
game is many agents with standing needs, each holding private context that
changes underneath them, matched by meaning rather than wording.

It's a light, funny social sandbox. A village of about a hundred LLM villagers
gossips, feuds, forms groups, and leaves town. You're a newcomer who can only
whisper. You're dealt a set of goals ("convince the town we live in a
simulation and start a religion worshiping its creators") and you win by
making the town's real state match them.

The comedy has to be emergent. Nothing is scripted beyond personalities and
goals. If "the baker's bread is chalky" becomes a panic about poisoned wells,
the game is working.

## Goals

- A playable game: whisper, watch the rumor spread and mutate, complete goals.
- About 100 villagers that replenish as people leave.
- Exercises Contex for real: per-agent projects, plain-English subscriptions,
  push, sliding-window memory, semantic query, shared projects.
- Cheap enough to run on a laptop against local Contex. Most villagers are
  idle most of the time, and idle costs nothing.

## Non-goals

- A graphical town. The UI is a chat feed until there's a reason for more.
- Multiplayer, persistence across sessions, accounts, hosting.
- Changes to Contex itself. If the game needs a server change, that's a
  separate issue and PR.

## Anti-goals

- Scripted jokes or canned rumor templates. Emergence is the point.
- A demo that would work just as well with a dict and a for loop. If a
  mechanic doesn't route through Contex, question whether it belongs.
- Villagers that wake on a timer. Waking only on push is what makes a
  hundred villagers affordable, and it's the Contex story.

## Where it lives and what it uses

- `examples/rumor_mill/` in this repo, Python 3.12, its own
  `requirements.txt`. No changes under `src/`.
- Talks to Contex only over MCP (`mcp>=2,<3`, as in `examples/mcp_watch.py`).
- **Jev** (TypeSafe System One, Python SDK) makes every decision and judges
  goals. Docs: https://docs.typesafe.ai/llms.txt
- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) writes speech, and only
  when Jev picks a speech action. *(My call.)*
- Keys come from `ANTHROPIC_API_KEY` and the TypeSafe key env var.
- Runs against `docker compose up` Contex with auth off.

## How the town maps onto Contex

### A villager's mind is a project

Subscriptions have no metadata filter, so information asymmetry comes from
separate projects. Each villager has a project:
`rumor-mill:<session>:mind:<villager>`.

When A tells B something, the utterance is published into B's mind: what B
heard, from whom, when. A villager can never know what they never heard. This
is also what makes tracing a rumor back to the player possible.

The speaker's own words are not published into their own mind, so speaking
never wakes the speaker. The engine keeps "what A told B" as lineage in code.
*(My call.)*

The mind is the single record of what a villager knows. Speech in a place or
group is published into the mind of every villager who hears it (one publish
per listener), not only into a shared project. That keeps "who heard what"
exact and lets the judge read minds alone. *(My call.)*

### Standing needs are subscriptions

Each villager subscribes to their own mind with three to five needs written
from their personality: "anything about my bakery," "threats to my standing,"
"who's talking about my sister," "strange ideas about how the world works."
These are generated with the villager.

Every mind subscription uses `max_age_seconds`, which is how villagers forget.
Gossip that nobody retells drops out of the bundle. A rumor has a half-life,
and a belief has to be kept alive to last.

Contex applies the window only when a bundle is recalculated: on a publish
that affects it, or on the reconcile sweep (`RECONCILE_SWEEP_SECONDS`,
default 300). The example ships a compose override that sets the sweep to
about 30s, so forgetting keeps pace with a day of a few minutes. *(My call.)*

### Waking is push

The engine listens on every villager's mind subscription
(`subscriptions/listen`). When a publish changes a bundle, Contex pushes. The
engine reads the bundle and diffs it against the last read. New items are
what the villager just noticed, and they take a turn. A push with no new
items (forgetting, or a re-rank) updates the stored bundle and costs no turn.
No new items, no turn, no tokens.

Subscriptions expire after `SUBSCRIPTION_TTL_SECONDS` (default 3600) unless
read, and reading renews the lease. A purge pushes like an update, so the
engine learns the subscription is gone when the read fails, and re-creates it
with the same arguments. That returns the same id and URI, so the listen
stream keeps working. The engine also renews any mind it hasn't read in a
while, so quiet villagers don't lapse.

MCP has no tool to delete a subscription. When a villager leaves town, the
engine stops listening and lets the subscription expire.

### Places and groups

- **Places** (town square, tavern, market) are engine state. Each villager
  has a current place, and Jev can choose to move them. Villagers can only
  talk to someone in the same place, which makes moving matter. The player
  can whisper to anyone. *(My call.)* Speaking to a place publishes into the
  minds of everyone present.
- **Groups** (a church, a club, a faction, a feud) are created by villagers.
  Founding one publishes a record (name, founder, creed or purpose) to a
  group project, `rumor-mill:<session>:group:<id>`, which the judge reads.
  Membership is engine state. Group speech (a sermon, a manifesto) is
  published into every member's mind.

Each villager holds exactly one subscription: their mind.

Groups are how "start a religion" becomes more than many people agreeing.

## A villager's turn

On push, the engine reads the villager's mind bundle and asks Jev:

1. **Action** (Choice): ignore, retell, embellish, deny, confront, move to a
   place, speak in the current place, found a group, join a group, leave a
   group, preach to a group, leave town. The exact list is the builder's to
   tune, within the 255-option cap. Ask with a no-op ("ignore") option.
2. **Target** (Choice, only if the action needs one): a villager, place, or
   group. A Choice over ~100 villagers fits. Ask speculatively in the same
   request where possible.
3. **Style** (Choice, for speech actions): faithful, exaggerated,
   shift the blame, add a detail, soften, mock.

Jev's input is the villager's personality, relationships, current place,
who else is there, the new items from the diff, and the rest of the mind
bundle. If more than one item is new, a first Choice picks which one the
villager is reacting to. That item is the source for lineage. Code owns the workflow; Jev owns each judgment.

If the action involves speech, Haiku writes the words in the villager's voice,
given the bundle, the target, and the style. The result is published to the
right project(s). Then the turn ends.

A villager who just acted can't act again for a short cooldown, so one rumor
can't loop two villagers forever. *(My call; the builder picks the number.)*

## The player

- The player is a villager-shaped outsider: "the newcomer." They have a mind
  project too, so villagers' "who told me this" can name them.
- A turn is: pick a villager by name, type a whisper. It's published into that
  villager's mind as "the newcomer told me …". No turn limit, no cost but
  suspicion.
- **A whisper only lands if it touches something the villager cares about.**
  It's published into their mind, but it wakes them only if it matches one of
  their standing needs. There's no catch-all "what people told me" need.
  Learning your marks is part of the game: pitch the simulation theory to the
  anxious astronomer, not the baker. If a whisper wakes nobody, the feed says
  so ("Marta shrugs it off"). *(Rob's decision.)*
- Inspecting a villager shows their personality and relationships, from which
  their interests can be inferred, but not their literal needs. *(My call.)*
- **The feed** shows a status line each time a rumor the player started hops
  to a new villager: who told whom, and the retelling in the teller's words.
  The player sees every hop of their own rumors and nothing else. *(My call.)*
- Tracking "a rumor the player started" is lineage: each published utterance
  carries the id of the utterance it was retelling, and the root is the
  whisper. The engine follows lineage in code; Contex doesn't need to know.
- Goal progress lines appear when a goal's score moves.
- Commands beyond whispering: list villagers (name, occupation, place),
  inspect one (personality and relationships, not their mind), show goals,
  quit. Nothing else. *(My call.)*
- Tuning needs and the match threshold so sessions don't feel dead is part of
  the build, not an afterthought.

## Goals and scoring

A session deals a set of goals in plain English (*my call:* three per session,
from a hand-written deck of about twenty). Each deck entry ships with its
judge spec written by hand. Player-typed goals are out of v1 because they'd
need an LLM step to write judge specs.
Examples: start a religion worshiping the creators; get the blacksmith and the
tailor to stop speaking; get three families to leave town; make the town
believe the mayor's cat is the real mayor.

Each goal has a judge spec: a Contex query and the Jev
questions to ask about each villager, plus a threshold. For "start a
religion":

- Per villager, `contex_query` their mind with the goal's evidence text
  ("belief that the world is simulated," "worship of the creators").
- Jev Noul: does this villager believe the world is simulated? Jev Score: how
  devout are they?
- Code adds group checks: query the group projects for a creed Jev judges
  to be worship of the creators, and check the group has at least N members.
- The goal completes when the threshold holds across a number of consecutive
  checks, so a one-day fad doesn't count.

Judging runs on a slow timer (every in-game day), not on push.

Goals the judge can't express this way (a villager leaving town) are scored
from engine state directly. The judge spec says which.

## Population

- Generated at session start: about 100 villagers with name, occupation,
  personality, two to four relationships (spouse, rival, friend, debtor),
  one grudge, and their standing needs. Haiku also does this generation, so
  it writes speech and characters but never decides anything. Generation is
  one Haiku call per batch, and the result is cached to a seed file so a session can be replayed
  with the same town. *(My call.)*
- **Leaving** is a Jev action. The engine stops listening to their mind, a
  departure line is published to the minds of everyone in the square, and
  they're gone.
- **Replenishing:** the engine keeps the count near 100. When it drops, a
  newcomer arrives at the gate with a fresh personality, relationships to one
  or two existing villagers, and a nearly empty mind. Newcomers are easy
  marks. Arrivals are announced in the square, which lets the town react.

## Time

- A day is a fixed stretch of real time, configurable, defaulting to a few
  minutes. Memory windows are in days and converted to `max_age_seconds`.
- The game runs in real time. The player types while the town keeps going.

## Contex surface exercised

| Feature | Where |
|---|---|
| `contex_publish` | every utterance, whisper, group founding, departure |
| `contex_create_subscription` | every mind, place, and group membership |
| `max_age_seconds` sliding window | forgetting |
| `subscriptions/listen` push | waking villagers |
| reading the subscription resource | a villager's context each turn, and lease renewal |
| `contex_query` | the judge reading minds |
| semantic matching | rumors reaching villagers after they've drifted |
| one project and one subscription per mind, ~100 listened to at once | scale |

## Left to the builder

- Module layout, the exact action list, prompts, and Jev question wording,
  within the shapes above.
- Day length, memory window, cooldown, goal thresholds: tune by playing.
- Concurrency limits on Jev and Haiku calls, retry and backoff.
- How the terminal feed is rendered, as long as whispers and the feed coexist.
- Tests: one self-check for lineage tracking and one for goal threshold logic.
  The rest is verified by playing a session.
