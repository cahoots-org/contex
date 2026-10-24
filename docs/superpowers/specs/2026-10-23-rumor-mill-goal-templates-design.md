# Rumor Mill: Goal Templates Filled by Jev — Design

**Status:** approved (Rob, 2026-10-23: "Yes")
**Replaces:** the per-hand Haiku goal writer from `2026-10-23-rumor-mill-targeted-goals-design.md`
(Generating goals) and the fixed goal deck. Everything else in that design stands: audiences,
believability bars, Long shots, swapping, marks, subjects, tracing and denials.

## Intent

Rob: "What about goals where a specific action has to occur?" Then: "Just mix them in with the rest
of the goals. No different treatment required. Also, are we not generating goals dynamically?"
Then: "Instead of that, why not create goal templates and let Jev fill them in?"

Every goal, the world goal included, is a template whose slots Jev fills from this village's real
people, groups and a pool of accusations and claims. Jev selects instead of writing, so a filled
goal is always valid, cheap, and quick enough for an instant swap.

## Goals

- Every goal is generated from a template; the fixed deck goes away.
- Action goals: something a villager does because of your rumor.
- A hand mixes kinds at random; no fixed slots, no special treatment for any kind.
- No validation-and-fallback path for model output during play.

## Non-goals

- New villager actions. Action goals use the actions villagers already take.
- Changing how belief is judged, how bars are set, or the hand-tipping rules.

## Templates

| Kind | Template | Filled by |
|---|---|---|
| Personal | Convince {mark} that {subject} {accusation} | code: subject; Jev: accusation, then mark |
| Circle | Get {share} of {circle} to believe {subject} {accusation} | code: subject; Jev: accusation, then circle |
| Village-wide | {Share} of the village believes {claim} | code: claim |
| Confront | Get {actor} to confront {subject} | code: subject; Jev: actor |
| Drive out | Drive {subject} out of town | code: subject |
| Start a group | Get someone to start a group against {subject} | code: subject |
| Join a group | Get {actor} to join {group} | code: group; Jev: actor |
| Exodus | Drive {n} villagers out of town (n from 2 to 4) | code: n |

- **The kind of each goal in a hand is drawn at random**, uniformly over the templates that can
  be filled right now. A template that can't be filled is skipped: "Join a group" with no group of
  2 or more members in town, a circle with no candidate of 3 or more. *(My call: uniform.)*
- **The subject is drawn by code**, weighted toward villagers who are the target of someone's
  grudge or a rival tie. It is never a villager already the subject of another of the player's
  goals.
- **Jev fills the rest with Choice questions**, in two requests:
  1. the accusation, chosen from the pool given the subject's trade, temper and grudges, as the
     one that would ring truest of them;
  2. the person or circle, given the subject and the chosen accusation:
     - the mark: who has the most reason to believe it of the subject;
     - the circle: which group would care most;
     - the actor: who would most plausibly confront the subject, or join the group.
  Options are real villager ids, circle ids, group ids or pool entries, so every answer is valid.
  If a Jev request fails, code picks uniformly from the same options. *(My call.)*
- **Circles** are founded groups with 3 or more members in town, and each place's regulars: the
  villagers whose routine puts them there at any phase. The labels are "the {group name}" and
  "the {place} regulars". The subject is excluded, and a circle needs 3 or more members besides
  the subject.
- **Claim goals** (personal, circle, village-wide) get `claim_text` = "{subject first name}
  {accusation}" or the claim, and a believer question "Does this villager believe {claim}?" Bars
  come from believability, as now.
- **Action goals** have no claim and are not judged by Jev. Each has an `action` (confront,
  leave_town, found_group, join_group or exodus), an `actor` where the template has one, and a
  `target` (the subject, or the group id). Badges show the actor as the mark and the subject.
  Their bar is the action happening; Exodus needs n.

## The pool

- **Haiku writes the pool once per village**, when the seed is first generated or when the pool
  file is missing: `{seed}.goals.json` with about 40 accusations and 15 village-wide claims fitted
  to this village's trades, places and grudges.
  - An accusation completes "{subject} …", such as "is stealing from the tithe box" or "waters
    down the ale". It is in the third person, has no other villager's name, and is under 80
    characters.
  - A claim is a whole statement about the village, such as "the well is cursed", under 80
    characters.
- The pool file is saved beside the seed and reused every run; Haiku is not called for goals in
  play.
- **A built-in default pool** is used when the file can't be written or read, and in tests. It
  is made of the old deck's claims plus about 20 general accusations.
- Pool entries are checked once when loaded. Entries with a second-person word, with a villager's
  name, or over the limit are dropped.

## Action goals are met by what happens

- After a villager acts, the engine reports the action to the judge: the actor, the action, the
  target, and the player whose whisper the action's source line roots in. An action whose source
  roots in no whisper is reported with no player and meets nothing.
- A player's unmet action goal is met when its action happens with that player behind it, and the
  actor and target match where the template names them:
  - Confront: actor confronts subject.
  - Drive out: subject leaves town.
  - Start a group: any villager founds a group whose creed names the subject by first name.
  - Join a group: actor joins the group.
  - Exodus: n villagers leave town with that player behind them while the goal is in play.
- A met action goal is met at once (`hold_checks` 1) and shows 100%.
- **Void:** the existing void rules apply. A goal is void when its actor or subject leaves, unless
  the subject leaving is the goal itself, or when its group dissolves.

## Settled for the builder

These are my calls, made after the builder check.

- **Credit.** An action counts when its source line roots in any whisper of the player's. The
  whisper doesn't have to be about the subject. A whisper only lands when it matches a villager's
  need, so the source is always a real rumor. Scaring the subject away with a whisper to them
  counts for "Drive out".
- **Swap** draws any template, not the same one.
- **Difficulty.** Action goals have no believability band. "Drive out" and "Exodus" are always
  Long shots, because leaving town is a rare decision. Exodus n is 2 or 3. These get revisited
  in the playtest.
- **Join a group.** The candidate actors are villagers in town who are not already members. A
  group that dissolves voids the goal.
- **Start a group** counts only when the creed names the subject. The creed comes from what the
  founder heard, so the player's whispers have to name the subject.
- **Action goals are a new `kind="action"`.** The judge doesn't query Contex for them, and
  overhear, baselines and tracing skip them. Their progress is what the engine reported: 0 or 1,
  or k/n for Exodus.
- **Uniqueness.** A filled claim's text must not equal one held by another player or the world
  goal. Taken accusations are left out of that subject's options before Jev chooses, so there is
  no retry.
- **Regulars** leave out the square and the gate. Everyone passes through the square in the
  afternoon.
- **Speed.** Believability is scored once per pool entry when the pool is loaded and saved in the
  pool file, as the believability of "someone {accusation}" and of each claim. A fill is then two
  Jev Choice requests.

## Hand-tipping rules

These are unchanged for claim goals. An action goal with an actor makes the actor a mark for the
badges; the whisper-to-mark Jev check and subject tracing apply only to claim goals, because both
need a claim. *(My call.)*

## The world goal

The world goal is a village-wide claim from the pool, not one held by a player now, at two thirds,
as today.

## What goes away

- `Writer.goals`, the per-hand Haiku call, `_validate` and its fallback.
- The fixed `DECK`, `rebar_deck`, `WORLD_CANDIDATES`, state goals and the religion goal. The deck's
  claims move into the default pool.
- `goal_writer_timeout_seconds`.

## Left to the builder

- Template wording beyond what is above.
- The Jev instruction text.
- The Haiku pool prompt.
- The grudge weighting.
- How place regulars are read from the routine.
