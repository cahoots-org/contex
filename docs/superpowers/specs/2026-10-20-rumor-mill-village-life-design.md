# Rumor Mill: Village Life — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-20
**Builds on:** `2026-10-19-rumor-mill-design.md` (the game as shipped in PRs #473-#475)

## Intent

Today the village is a stage that only moves when you whisper. Between whispers
nothing happens, villagers have no places they belong to, and "relationships"
are four fixed labels. This adds a village that lives on its own: people have
homes, routines, neighbors, families, friendships and feuds; they marry, have
children, and die; children are people too, and the riskiest rumor carriers in
town.

The player's job stays the same: whisper, watch the rumor travel, complete
goals. What changes is what they have to read. A rumor now travels along a
social graph the player has to work out by watching who lives near whom, who
drifts together, and who is related, and the graph itself changes as people
fall out, marry, and die.

## Goals

- Stakes: the player can lose. Villagers can work out who is behind the rumors and run
  the newcomer out of town (see Suspicion and losing).
- A village that visibly moves and interacts between whispers, at no model cost.
- Relationships that matter mechanically: they decide who spends time together
  and who passes a rumor to whom.
- Hidden structure the player uncovers through play. Obvious facts (family,
  marriage, where someone lives) are shown. Scores and strength of ties are not.
- Families, marriage, births, and deaths, so the village changes over a session.
- Children the player can whisper to: easy to reach, fast and indiscriminate
  spreaders, bad at keeping a secret or a source.

## Non-goals

- A graphical map or avatars. The sidebar's villagers-by-place view is the map.
- A body for the player. The player still whispers to anyone from anywhere.
- An economy, property, jobs that change, or a calendar of festivals.
- Rumor goals about children (see Children).
- Changes to Contex itself.

## Anti-goals

- A per-tick model call. The ambient simulation must cost zero tokens; the only
  model calls it may cause are the rare, bounded relay lines described below.
- Showing relationship scores or villagers' needs anywhere in the UI or API.
- A simulation that wakes villagers on a timer to think. Villagers still take
  Jev turns only when something new reaches their mind. The tick moves them and
  accrues time together; it does not make them decide.
- Realism for its own sake. If a rule does not change what the player sees or
  can do, leave it out.

## Suspicion and losing

The newcomer is not anonymous. Every whisper tells its recipient who it came from, and
rumors can carry the source forward. If enough of the village works out that the
newcomer is behind the gossip, they run the newcomer out and the game is lost.

- **Per-villager suspicion.** Each villager holds a suspicion in [0, 1] that the newcomer
  is behind the rumors, stored with game state. It never starts above zero.
- **What raises it.**
  - Being whispered to: the recipient knows the newcomer told them. A whisper that
    lands or shrugs both count; probing has a cost.
  - Hearing a player-rooted utterance that names or implies the newcomer ("the newcomer
    told me ..."), including from a child, which weighs more.
  - Rumors that turn out to be sensational or hurtful to someone they know.
  - The rise is judged by the existing Jev turn request: when a villager's new items
    include a player-rooted utterance, the request gains one Score question about how
    strongly it makes them suspect the newcomer is behind these rumors. No extra call.
- **What lowers it.** Slow decay each game day, and quiet: a villager who hears nothing
  new about the newcomer for a stretch relaxes. *(My call.)* There is no cheat to clear it;
  laying low is the counterplay, and so is making someone else look guilty (a rumor that
  points suspicion at a villager is judged like any other and can lower suspicion of the
  newcomer in the people who believe it).
- **Spread.** Suspicious villagers pass it on. A villager over a threshold may warn
  others (an ordinary retell whose text is about the newcomer, so it is itself a rumor
  that spreads along ties and reaches children), refuse to listen, or confront the
  newcomer. Jev chooses these from the actions offered; the action set gains `warn` for
  villagers over the threshold.
- **A suspicious villager turns away.** Whispering to a villager over the refusal
  threshold fails with an in-character refusal and still counts as a whisper they
  remember (it raises their suspicion).
- **Losing.** Town suspicion is the sum of each villager's suspicion weighted by
  influence (everyone counts for one; a small set of influential roles, such as the
  priest, the innkeeper and the mayor, count for more). When it holds above the run-out
  threshold for several consecutive checks, the town meets and runs the newcomer out. The
  game ends with a summary: goals and progress, and what gave the newcomer away (the first
  hops where the newcomer was named, from lineage), so the loss explains itself.
- **Warning.** A loss with no warning feels unfair. The player gets two signals: the
  villagers' own behavior (warnings, refusals and confrontations in the feed), and a coarse
  four-level label in the sidebar (quiet, murmurs, watched, hunted). It is a label, never a
  number, and it does not say who suspects. *(My call.)*
- **Winning** is unchanged: complete the dealt goals. Losing ends the session; the player
  can start again.
- **Cost.** One extra Score question per player-rooted hearing, inside a turn request that
  already exists. The influence weights and the check are plain code.
- **Interaction with village life.** Children blab the source more often; kin warn kin;
  births, deaths and weddings give the village things to talk about besides the newcomer,
  which is a way to hide. A villager's affinity toward the newcomer does not lower their
  suspicion by itself, but warm villagers are likelier to keep it to themselves.

## Naming

`Lineage` in the code is the rumor trace (which utterance retells which). The
family tree is called **kin** everywhere in the new code and docs so the two
are never confused.

## Geography

- Each household has a **home position** `(x, y)` in the unit square, generated
  at village creation; the shared places (`square`, `tavern`, `market`, `well`,
  `mill`, `chapel`, `gate`) have fixed positions. Distance is Euclidean.
- Neighborhoods are not stored: neighbors are whoever lives near, computed from
  distance when needed.
- Distance drives three things: who a villager runs into (encounter weight falls
  with distance), how warm two villagers start (nearer starts warmer), and how
  far a villager will travel for leisure.
- Spouses and children share their household's home. Newcomers get a home at the
  edge of the village.
- *(My call.)* Homes are positions, not places in `PLACES`. A villager who is
  "at home" is in a per-household place that only household members share, so
  being at home never makes two unrelated villagers present with each other.

## Routines and movement

A seeded simulation tick moves villagers who are not in the middle of a Jev turn.

- **Routine.** Each villager is generated with a work place, a leisure place, and a
  `sociability` between 0 and 1, chosen by Haiku in the same call that writes
  their character, from their occupation and personality. This avoids a
  brittle table from free-text occupations ("Hedge knight (retired)") to places.
- **Day phases.** A game day has four phases (dawn, day, evening, night). Work
  place by day, leisure in the evening, home at night, with a short wander at
  dawn. The tick runs every `sim_tick_seconds` (default one eighth of a game day).
- **Pull.** Within a phase a villager's destination is drawn from the routine
  place and nearby alternatives, weighted by the summed affinity of the
  villagers already there (Relationships) and by distance, scaled by sociability.
  A villager with no scores yet follows the routine.
- **Precedence.** A move Jev chooses when a villager wakes holds until the next
  phase boundary. A villager who took a turn within the cooldown is not moved by
  the tick.
- **Determinism.** Given the seed and the clock the tick is reproducible. The
  clock is injected so tests do not sleep.
- The tick publishes nothing to Contex and calls no model.
- *(My call.)* Jev can choose a place to move to but not "home". Home is entered by the
  routine (night) or a funeral/wedding override only. In the sidebar every villager who
  is at home shows in one collapsed "At home" bucket, so households do not become thirty
  sections. Internally home is a per-household place accepted by `Town.move` for the
  simulation.

## Encounters

Two villagers in the same place at a tick are **together** for that tick.
Time together is the raw material for relationship drift and for ambient relay.

## Relationships

- **Score.** Each villager holds an affinity in [-1, 1] toward each villager they
  have met, stored sparsely and asymmetric (A can like B more than B likes A).
  It lives in game state, not in Contex.
- **Start.** From distance (nearer is warmer), kin (high), shared household
  (highest), and trait compatibility. Villagers get a handful of hidden traits at
  generation (for example gossipy, devout, thrifty, bold, trusting, each 0-1);
  compatibility is how close two villagers' traits are.
- **Swing.** The closer two villagers are geographically, the more often they
  meet, so the more their scores move. Compatibility sets direction: similar
  villagers drift toward friendship, dissimilar ones toward friction.
- **Updates.** Two sources, both weighted:
  1. Time together: a small drift per tick, scaled by compatibility.
  2. What was said: when a villager hears an utterance, their existing Jev turn
     request gains one question per distinct speaker among the turn's new items (at
     most three, the most recent; the newcomer and the town are skipped), each a graded
     warmth/hostility Score about that speaker's words. *(My call.)* The shift is weighted by the utterance's word count and
     scaled by compatibility. No extra model call.
- **Bounds.** Scores decay slowly toward the villager's baseline and saturate near
  the ends, so friendships cannot run away on feedback (friends meet more, so
  grow closer, so meet more).
- **Effects.** Affinity weights move pull, the odds of a villager choosing a
  target, and ambient relay.
- **Visibility.** The inspect card shows the obvious ties: household, kin, spouse,
  where they live. It never shows scores. The player learns the rest by watching
  who gathers where and who tells whom.
- Falling-outs and friendships that cross a threshold are published as
  announcements into the minds of the villagers who would care, so a feud is itself
  a rumor.

## Ambient relay

When a tick finds villager A, who holds a live rumor, together with B, who has
not heard it:

- Code rolls a chance scaled by A's affinity for B, A's sociability, and a base rate.
- Only rumors the player started (lineage root is a whisper) are relayed ambiently, and
  B has "heard" a rumor if the engine delivered any utterance with that root into B's mind,
  whether or not it matched. The engine keeps that set per villager. So cost scales with
  whispers, not with chatter. *(My call.)*
- On a hit, code builds the decision itself (retell, source = A's rumor, target = B,
  style from A's discretion) and runs the normal effects path. Haiku writes the line.
  No Jev call, so the cost is one small Haiku call, about $0.0004 as measured for
  shrugs.
- A cap per tick and per minute bounds the volume. The relay is delivered into B's
  mind like any utterance, so B may wake and take a normal Jev turn from there.
- A rumor the player started stays traceable through lineage as it spreads this way,
  and hops print in the feed as they do now.

## Households and kin

- Villagers are generated as **households**: a family name, one to five members,
  ages, and relations (spouse, parent, child, sibling). Existing relationship kinds
  (`friend`, `rival`, `debtor`/`creditor`) stay and are created between households.
- Kin start with high affinity. A household-level **feud** edge can exist between two
  families; members start cold toward each other and children inherit it. Family
  grudges are what the existing `grudge` field already hints at; they become real.
- The kin tree is shown on the card (obvious). Scores are not.
- This replaces the loose-individual generation. It changes the seed file format, so
  an old seed is refused with a message to regenerate (the existing size-mismatch
  check pattern).

## Addressed delivery

Rumors still reach villagers only by meaning: a whisper or retelling wakes a villager
only if it matches one of their needs. Some messages are addressed to a person, not
broadcast: a proposal to its target, and a birth, death, or marriage notice to kin.
Those must not depend on a match.

- Slice 0 builds this mechanism (for warnings); marriage and kin notices reuse it.
- The engine delivers an addressed message into the recipient's mind as usual (so it
  is part of what they remember and can be matched later) **and** records it in a
  per-villager pending list. The pending list sets the villager's wake event and is
  added to the turn's `new` items, so Jev sees it whether or not Contex matched it.
- Everyone else (neighbors, the square) receives the same notice by meaning only and may
  or may not wake. This is the village noticing news, not being told.
- *(My call.)* This replaces the earlier idea of a per-villager "news about my family"
  need. It avoids depending on whether a death notice clears the wake threshold for a
  need about kin, and it avoids changing a villager's needs (and so their subscription,
  which Contex keys by its arguments) when they marry into another household.

## Marriage

- New Jev actions `propose` and `accept`, plus `decline`. A proposal is an addressed
  message (see Addressed delivery) held as a pending proposal on the target; their next
  turn sees it in `TurnView` and decides `accept` or `decline`. A pending proposal
  lapses after a game day.
- Eligible: both unmarried adults, not close kin, with high mutual affinity. Eligibility
  is enforced in code; Jev chooses among the actions offered.
- On acceptance: both become spouses (reciprocal kind), share a household, move to one
  home, affinity rises, the marriage is announced into the minds of the square and of
  both families. A wedding can pull kin to the chapel for a phase.
- Widowhood clears the spouse tie and permits remarriage.
- Divorce is out of scope.

## Aging, births, and deaths

- **Time.** Ages are game years. `year_days` (default 5 game days) sets the length so
  that a 10-30 minute session sees a few births and deaths. At the shipped game-day
  length of two minutes, that is a ten-minute year. *(My call.)* A child born during a
  session will not reach age 6 within it at the default; babies are news, and child agents
  come from the seed (ages 6-15). The age-6 path exists for long sessions or a smaller
  `year_days`.
- **Stages.** Infant (0-5): a record in the household, not an agent. Child (6-15): an
  agent. Adult (16-59). Elder (60+).
- **Births.** Each game year, a married couple whose younger spouse is under 45 has a
  chance of a birth, scaled down by household size. The birth is announced into the
  minds of kin and neighbors. At age 6 the infant becomes a child agent: Haiku writes a
  name, personality, and needs from the parents' profiles (one call), traits are a
  blend of the parents', and the child inherits the household's feuds.
- **Deaths.** A yearly hazard rising with age for elders, plus a small accident chance
  at any age. A death removes the villager permanently (the same path as leaving town,
  without a newcomer), clears their spouse tie, and is published into the minds of kin
  and neighbors. Kin gather at the chapel for a phase.
- **Wake on family news.** Birth, death, and marriage notices are addressed to kin (see
  Addressed delivery), so family always hears. Neighbors hear by meaning only.
- **Population.** Births and deaths move the count. The replenisher still tops up
  toward `population`, but only when the village falls below 90% of it, so a death is
  not replaced the same minute.
- Death is not a player goal target and the player cannot cause it directly.
- *(My call.)* Deaths go through the same removal as leaving town but are recorded in a
  separate `died` list. The goals that count departures (villagers or families leaving
  town) count `departed` only, so waiting for old villagers to die cannot win them. With
  households, a "family" is a household, and it counts as having left when every living
  member has left town. Children cannot choose `leave_town`, `found_group`, or
  `propose`.

## Children

Children (6-15) are full agents the player can whisper to. They are the risky way to
spread a rumor.

- **Easy to reach.** Their needs are broad and childlike: secrets, who is in trouble,
  fights, grown-ups behaving oddly. A rumor with any scandal in it tends to land.
- **Indiscriminate.** Their relay chance does not depend on affinity much, and their
  targets include whoever is present: parents, the rumor's subject, a rival. Their
  Jev state marks them as children, and the action instructions say children tell
  things to whoever is nearby.
- **Distorting.** Their style odds lean toward exaggerate, add a detail, and mock.
- **Bad at secrets.** They are more likely to say who told them ("the newcomer told
  me"). The Jev state for a villager who hears a rumor from a child says it came from a
  child, so an adult may weigh it lower. *(My call.)* Suspicion stays flavor: it lives
  only in what villagers remember and say, with no new player-facing state or penalty.
- **Age-appropriate by construction.** A child's lines are always age-appropriate. The
  writer prompt for a child requires it, a rumor with adult content comes out as the
  garbled, kid-level gist ("they were being weird at the tavern"), and a post-check
  rejects lines that fail a simple age-appropriateness check and falls back to the
  garbled gist. No goal in the deck targets or concerns children.
- **Routines.** Children play at the square, the well, and the mill by day, are home at
  night, and follow their household's feuds.
- The sidebar and card show a child's age stage.
- *(My call.)* Belief goals judge adults only (age 16 and up). Children are excluded from
  both the numerator and the denominator, so whispering to children cannot inflate a goal
  and the judge's cost does not grow with them. Children still spread rumors to adults,
  which is the point of them.

## Cost

- The tick, movement, encounters, and relationship drift: no tokens, no Contex traffic.
- Ambient relay: one small Haiku call per relay, bounded by the caps.
- Generation, once per seed: households with routines and traits. About the size of
  the current village generation plus routines and ages.
- Each child agent born during a session: one Haiku call at age 6.
- Jev: the extra warmth question rides on an existing turn request; at the quoted
  price, input tokens are the only cost and they are small.

## UI

- Card shows household, kin, age stage, where they live, and the routine place they
  are at; never scores, never needs.
- Villager updates are batched every few seconds (the page already re-renders only
  when the set of (id, place) changes).
- Births, deaths, marriages, arrivals, and falling-outs appear in the feed as town
  announcements.
- No map and no new view.

## Contex surface exercised

Same as the shipped game. New: family, birth, death, marriage, and falling-out
announcements are published into the minds of the villagers who would care and reach
them by meaning; addressed delivery is the one place the engine wakes a villager without a
semantic match, and it is limited to messages addressed to that villager.

## Changes to existing code the builder should expect

- `Engine` reads the clock and a seeded random generator from injected parameters (today
  it calls `time.monotonic()` directly and `decide` uses global `random`). Only the tick,
  given a state and a clock, is reproducible; a whole session is not, because Jev output
  and async timing vary.
- The engine exposes per-villager turn state to the tick (last turn time, turn in flight,
  a pin that holds a Jev-chosen move until the next phase). A tick can move a villager
  during their own Jev call; `act` already drops a directed utterance whose target left,
  and that gets likelier.
- `Villager` gains household, home, traits, routine, sociability, age, and spouse. Spouse
  leaves the random relationship wiring. `TurnView` gains pending addressed messages,
  eligibility facts for marriage, and an age stage; the action set depends on them.
- The seed file gets a format marker (an object with a version and households), so an old
  flat seed is refused with a clear message. Infants are not agents and do not count toward
  `population`; children do.
- The replenisher's top-up floor becomes 90% of `population` (a deliberate change), and
  deaths use the same removal path and departure queue without triggering a same-minute
  replacement.
- Newcomers arrive as single-person households at the edge of the village with a routine
  and traits; no random relationships to existing villagers beyond a neighbor tie.
- Birth, death, marriage, and falling-out items go through `Engine.deliver` as announcements
  so the feed and the page show them.

## Slices

Each is its own PR-sized change, in this order. Suspicion comes first because it works on
the shipped game and gives the player something to lose; village life builds on it.
0. Suspicion and losing: addressed delivery (a warning must reach its target without a
   topic match), per-villager suspicion, the extra Score question, `warn`, refusal, town
   suspicion with influence weights, the lose state and summary, the sidebar label.
1. Households, homes, routines, and the movement tick with encounters (new generation
   and seed format; no scores yet).
2. Relationship scores, the warmth question, move and target pull, and ambient relay.
3. Kin on the card and household feuds (kin notices reuse addressed delivery from slice 0).
4. Marriage.
5. Aging, births, deaths, and children as agents.

## Left to the builder

- Suspicion constants (rise and decay rates, thresholds, the influence weights and which
  roles count more), the wording of refusals, and the layout of the loss summary.
- Exact trait set, score constants, decay and saturation shapes, relay base rate and caps.
- Tick length, phase boundaries, routine wander rules.
- Generation prompts for households, routines, and children.
- The age-appropriateness post-check's implementation, as long as a failing line falls
  back to the garbled gist and never prints.
- Tests: tick determinism with an injected clock, score updates and bounds, relay
  bounds, marriage eligibility, birth and death effects on the household and the
  village count, child rendering rules, seed refusal.
