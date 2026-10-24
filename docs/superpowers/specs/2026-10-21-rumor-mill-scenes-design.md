# Rumor Mill: Scenes, a Moving Village, and Knowing Where You Stand — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-21
**Builds on:** PR #478 (following, overhearing, talking; 50 villagers; warmer village; memory measured in
the player's moves; goal counts; news carried on a move; the visible day clock)

## Intent

Playtests after PR #478 surfaced four complaints, in Rob's words:

- "It feels really good when a rumor actually takes hold and spreads, but there isn't
  enough feedback for the other cases. Honestly even the really good scenario could use
  more visual feedback." A rumor passed from Ysabella to Rohesia "died right there", with
  nothing saying why.
- "It's hard to gauge whether I'm on the right track or not." "I want to be able to get a
  feel for which villagers to target. I also want to be able to see who is affected by
  each of my rumors (by selecting the rumor in the chat panel)."
- "Townspeople should move around regularly. Otherwise, how are rumors supposed to spread?"
- "I want to be able to continue a conversation if they brush me off." And a heated
  exchange between Aldwin and Alicia about market taxes stopped after one back-and-forth:
  "Conversations should keep going if they have momentum and potentially draw in others
  nearby." A scene "should follow the conversation until it veers off topic."

## Goals

- A rumor that catches produces a visible scene; one that dies says why.
- The player can see, per rumor, who it touched, and whether it is moving a goal.
- The player gets a feel for whom to target from what they have learned in play, never
  from hidden data.
- Villagers move through the day, so rumors can travel between places.
- A brush-off is an opening, not a dead end; near misses get a reaction.

## Non-goals

- Households, homes, relationships, families, births and deaths (the village-life
  design's later slices).
- Revealing needs or any numeric score to the player.
- Changes under `src/`.

## Anti-goals

- Previewing who would bite before the player whispers (rejected earlier: knowledge must
  be earned in play).
- Decoration that says nothing (rejected: per-face speech bubbles, a village-wide glow
  while several rumors run).
- Unbounded model calls from scenes.

## Part A: Gameplay

### A1. Scenes with momentum

- **A scene** is a conversation at one place among villagers who keep replying to each
  other about one topic. It starts with an **exchange**: a line said to someone (retell,
  deny, confront, warn, or speak to the room) whose listener's next turn answers it with a
  speech action of their own at the same place. Both kinds of talk start scenes (Rob):
  - a player-rooted, on-topic line, anywhere in the village;
  - ordinary village talk, only at the player's current place.
  At most `max_scenes` (Config, 3) run at once; a new exchange beyond that stays ordinary
  talk. A scene's **topic** is its opening line; for a player-rooted scene that is the
  whisper, so the scene shows on that rumor's card.
- **Momentum** is a number per scene. It starts from the opening line's heat; each turn
  inside the scene adds the participant's answer to one new Jev Score question riding on
  their existing turn request ("how much does this villager want to keep talking about
  this?"), plus a bonus for heated or lively styles and for a new participant, and loses a
  fixed decay per line. *(My call: Score levels "not at all" to "can't stop", normalized
  like the suspicion Score.)*
- **While momentum is above the floor,** participants are addressed directly: the next
  line in a scene goes to the other participants (and the room, for speak) through the
  addressed path, so they wake without a needs match. This is what lets a heated exchange
  keep going. (Addressed lines already skip fatigue.) Inside a scene the turn cooldown is
  `scene_cooldown_seconds` (Config, 5) instead of 30, so a 12-line scene plays out in about
  a minute, not past a game day.
- **A scene turn is a normal turn.** Jev may still pick any action. A participant whose
  turn is not speech (ignore, move, leave, a group action) leaves the scene, and momentum
  takes an extra drop. A participant who moves may carry the news (the PR #478 rule). *(My
  call.)*
- **Drawing others in.** Each scene line is also addressed to each bystander at the place
  with chance momentum × `join_rate` (Config, 0.3). Being addressed wakes them; if their
  turn answers with speech about the scene they join it. No extra Contex queries: a
  bystander whose needs the line matches hears it anyway through their subscription, and
  joins the same way. *(My call: cap at 6 participants.)*
- **Ending.** A scene ends when momentum drops below the floor, when the line count reaches
  `scene_max_lines` (Config, 12), when fewer than two participants remain at the place, or
  when it **veers off topic**: a line that fails the on-topic check against the scene's
  opening line (the same measure and `on_topic_margin` rumor cards use) ends it ("The
  argument turned to other things.") and the scene stops being addressed; anything after
  that is ordinary village talk under the overhearing rules.
- **Showing it.** A player-rooted scene shows on its rumor card as one grouped block ("An
  argument at the tavern: 4 people, 9 lines"), collapsed like retellings. A village-talk
  scene shows where overheard talk shows, as its own block at the player's place, every
  line of it heard (not sampled), since the player is standing there.
- **Cost:** one Jev request and one Haiku line per scene line (as for any turn), capped by
  `scene_max_lines`; the momentum question adds a few input tokens.

### A2. A brush-off is an opening

- After a brush-off, the next whisper to that villager while the thread is open (until the
  next part of the day) is a **reply** in the same thread. It is checked as any whisper is,
  **and also** against the need the hint came from with a lower bar (`hint_margin`, Config,
  0.0, i.e. at the need's baseline); either one landing counts. If it lands, the villager
  engages as in a conversation, on the same card (the existing conversation threading); if
  not, a second brush-off; at most two tries per thread. Suspicion rises per message as for
  any whisper.
- **Near misses** (clearance within `near_miss_band` below the bar, Config 0.01) get a
  short in-character "go on…" reply instead of a brush-off, and the next message is a reply
  as above. The direct check's Contex query threshold drops to baseline + `match_margin` −
  `near_miss_band` so near misses come back; hits are still decided against the full bar.
- **Hints keep the subject recognizable**: they may name the topic (strange lights, the
  children in my care, the vines) without specifics; the allusion rule now forbids names,
  numbers and particulars, not the subject.

### A3. A village that moves

- Each villager gets a **work place** and a **leisure place**, chosen once per villager by a
  Jev Choice over the places from their occupation and personality, cached in a sidecar next
  to the seed (`<seed>.places.json`, no seed regeneration). Villagers who arrive later get
  theirs on arrival, written to the sidecar.
- The PR #478 **day clock** (morning, afternoon, evening, night) moves villagers who are not
  in a turn or a scene: work in the morning, the square in the afternoon, leisure in the
  evening, their work place at night (a stand-in for home until homes exist). Moves happen
  in small batches spread across the first part of each phase, not all at once. A move the
  villager chose on their turn holds until the next phase boundary.
- A routine move uses the same carry rule as a chosen move: with the villager's share
  chance, they share the freshest news they hold (their latest new item) with someone where
  they arrive. That is the only model call a routine move can cost.
- Moves publish nothing themselves and show in the village panel as today.

## Part B: Feedback and knowing where you stand

### B1. The rumor's frontier

- Each card shows its **frontier**: the people who hold the rumor now, each with an outcome
  in words, as the player's whisper gets: "heard it, didn't care", "busy, hears it in about
  18s", "kept it quiet", "passed it on to Tom Brewer", "in an argument at the tavern".
- When the frontier empties, the card says **why it stopped** in one line ("It stopped with
  Rohesia: it didn't touch anything she cares about." / "Everyone who heard it had heard it
  before." / "It wore out after five tellings.").
- A room hop shows one summary ("14 heard it, 2 cared").
- "Didn't care" is decided by the direct check the player's whispers use, run for each
  listener of an on-topic one-to-one hop (one needs query per need, no model calls). Room
  hops show "14 heard it, 2 passed it on", counting reactions, not caring.

### B2. Select a rumor, see who it touched

- Clicking a rumor card (or pressing Enter on it) selects it; the village panel then marks
  every villager it touched, with a mark and a word: heard it, passed it on, believes it,
  in a scene. Tiles show counts for the selected rumor. Escape or clicking again clears.
  One rumor at a time.

### B3. Am I on the right track?

- Each card says **which goal it serves** (the goal whose evidence the whisper is closest
  to, decided by the existing goals query; none for goals without evidence text, such as
  leave-town goals) and **who it has convinced** ("2 now believe the tavern is haunted"). A
  believer counts toward the one rumor whose line is their best-matching memory for that
  goal, found by the lineage root of that memory; a believer whose best memory is not from
  a player rumor counts toward none.
- Goal progress lines in the feed name the rumor that moved them.

### B4. A feel for whom to target

- From what the player has learned only: each villager's card and row show the player's
  notes (status, every hint from brush-offs, what they passed on; today `Notes` keeps only a
  status and the latest hint, so this extends it), whether they are
  **influential** (the weighted roles already used by suspicion: shown as words, e.g.
  "folk listen to the priest"), and whether they **believe** any of the player's goals.
- The village panel gets a **sort**: by the player's notes ("took the bait before" first),
  by influence, or by place.
- While writing a whisper, villagers whose **earned hints** match the draft (embedding
  similarity of the draft to the hint lines the player has seen, computed by a Contex query
  over a per-session `hints` project) get a quiet mark: "this might interest them". It
  never uses their needs.

### B5. Moments

- A warm feed line when a rumor crosses a threshold (5, 10 people), reaches an influential
  villager, starts a scene, or moves a goal; the same weight for bad moments (someone
  warned about you, a rumor turned against you).

## Order

Part A first (scenes, the brush-off opening, movement), then Part B, because scenes and
movement change what the frontier and the panel have to show. Each part is its own PR.

## Left to the builder

- Momentum constants, the join chance curve, phase lengths and batch sizes, copy.
- How the frontier and scenes lay out inside a card, within the page's existing design.
- Tuning in a real-model session per part, with the playtest cases above as the bar: the
  Aldwin/Alicia market-tax exchange keeps going and pulls in a neighbour; the
  Ysabella/Rohesia hop says why it stopped; a whisper to a moving villager spreads to a
  second place.
