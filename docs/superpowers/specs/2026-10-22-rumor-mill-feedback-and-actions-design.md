# Rumor Mill: Knowing Where You Stand, and More to Do Than Whisper — Design

**Status:** approved to build (Rob, 2026-10-22: "Let's build the things we planned, THEN we'll do a
playtesting pass")
**Builds on:** `2026-10-21-rumor-mill-scenes-design.md` Part B (feedback), updated here for the
shared world in `2026-10-21-rumor-mill-multiplayer-design.md` steps 1–2 (built), plus new actions.

## Intent

Rob: "It's hard to gauge whether I'm on the right track." "I want to be able to get a feel for which
villagers to target. I also want to be able to see who is affected by each of my rumors." "We need to
give players more actions they can use … What else can we allow them to do besides whisper?"

The player should be able to scout before they whisper, check on a rumor after, and always see why a
rumor is spreading or why it stopped. Everything here is per player: no player learns anything about
another player.

## Part B, as designed, now per player

B1–B5 are the scenes design's Part B, unchanged except:

- **Everything is the player's own.** The frontier, why it stopped, the selection marks, goal links,
  notes, the targeting sort and marks, and moments all come from that player's rumors, notes and
  goals, and reach only that player.
- **B3 goals** are the player's personal goals; the world goal is linked too when a rumor serves it.
  "Who it has convinced" counts the judge's believers for that goal whose best-matching memory roots
  in this rumor.
- **B4 hints** live in one Contex project per player (`rumor-mill:{session}:hints:{player}`), holding
  the hint lines that player has seen (brush-offs, near misses, and answers from Ask around).
- **B5 moments** include one more good moment: a rumor reaching another place than where it started.

## New actions

### Walk

- Go to any place: the page's place tiles get a "Walk here" button, the terminal a `go <place>`
  command. It costs nothing and raises no suspicion. Whispering still takes you to the villager's
  place, as today.

### Listen

- At your place, listen for a while (`listen_seconds`, Config, 20 real seconds): every line spoken
  there reaches you, not a sample, shown in one "You listen at the tavern" block. The block opens with
  the last 5 lines said there in the past game hour (from the game's own record of utterances), so
  listening is never silent. No model calls; no suspicion. One listen at a time per player.

### Ask around

- Ask a villager **at your place** what folks have been saying about something ("What have you heard
  about the mill?"). They answer from what they actually heard (the existing recall of heard lines,
  against the topic), naming who told them and where, in one in-character line. A villager who heard
  nothing about it says so in character. The answer is added to the player's notes and hint lines.
- Asking raises suspicion of the asker by half a whisper's bump: a newcomer asking questions is
  noticed, but less than one spreading stories. It is charged to the asker's cost.
- The question is not published to the villager's mind: asking never spreads anything.

### Not now

- Wait and Sleep: the clock runs on real time (Rob), so they have nothing to do.
- Leave a note and Lie low: later, if the playtest wants them.

## Non-goals

- Restricting whispers to villagers at your place.
- Any change to how whispers land.
- Multiplayer steps 3–5 (moderation, persistence, hosting): after the playtest.

## Left to the builder

- Copy, layout within the existing page, the frontier's compact form on a busy card, the moment
  thresholds' exact lines, and how Listen's block sits beside overheard talk.
