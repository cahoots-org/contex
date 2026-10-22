# Rumor Mill: A Page You Can Read at a Glance — Design

**Status:** approved (Rob, 2026-10-22: layout A with the conversation dock — "That looks good")
**Mockups:** `.superpowers/brainstorm/40543-1792689318/content/glanceable.html` (option A) and
`conversations.html` (the dock).

## Intent

Rob: "We need to talk about the UI. It's a hot mess." "Most of the text just goes right past me. I
read almost none of it. Also, the rumor status updates don't tell me which rumor started
conversations." "If someone leans in I'd like to continue the conversation to see if I can get them
to believe the rumor. However, I don't know how to do this without cluttering up the UI."

The page grew by accretion: one feed carries everything, rumor cards grow in place, and toggles,
chips and counts are everywhere. The fix is to give each part of the screen one job, show how
rumors are doing with shapes and colour rather than sentences, and keep words for the moments worth
stopping for.

## Goals

- A player can tell how each rumor is doing, and which rumor caused what, without reading.
- Rumors (things spreading that you watch) and conversations (villagers talking to you that you
  join) live in different places.
- Continuing a conversation is one click and typing.
- The feed is short: most lines are quiet one-liners; only a few things are loud.

## Non-goals

- Any change to game rules, events or the server's data, beyond small additions the page needs
  (listed below).
- A village map (option B was not chosen).

## The layout

Header (unchanged in content): title, mood, trophies, the day wheel and clock, your place, your
cost; goals as cards below it.

Three columns, then the dock and whisper box across the bottom:

1. **Your rumors** (left). One row per rumor: its **mark**, a big number (how many have heard it),
   a bar toward the goal it serves in the rumor's colour, and a short goal word. A rumor that has
   stopped is dimmed. Selecting a row (click or Enter) opens it **in place** — only one open at a
   time — showing its one-sentence story, its goal with how many it convinced, who has it (grouped,
   as today), and the conversations about it going on now; and rings the villagers it touched in
   the village column (today's selection marks, in the rumor's colour). *(My call: in-place
   expansion, not a side panel, so nothing else on the page moves.)*
2. **What you hear** (middle). The feed, made short:
   - **Loud** (bold, full text): moments, a warning about you, a trophy, a goal met, the game's end.
   - **Speech** (one line, quoted): a villager's words you overheard or that answer you.
   - **Quiet** (dim one-liners): everything else — a retelling of your rumor ("Hob told Marta at
     the tavern"), a conversation started or ended ("A conversation at the chapel · 4 people"),
     village talk at your place ("Village talk at the square · 2 lines", opening on click), listen
     recaps.
   - Every line about one of your rumors starts with that rumor's mark. Your whispers no longer
     appear in the feed: they are the rumor rows.
3. **Village** (right). Places and people as today, with the selected rumor's faces ringed in its
   colour, and the walk/listen/ask actions as today.

**The dock** (above the whisper box): one face for each villager in a conversation with you now,
with a badge for where it stands — leaning in (…), answered (↩), brushed off (–) or busy (◷). Clicking
a face opens that exchange as a short chat right above the box, tagged with the mark of the rumor it
is about, and points the box at that villager ("Talking with Agnes"); typing continues the
conversation. A conversation leaves the dock when it ends. The dock is empty when nobody is talking
to you.

## Rumor marks

- Each rumor gets a colour and a glyph when it is whispered, from a fixed set of six pairs (amber ◆,
  teal ●, violet ▲, rose ■, green ★, sky ✚), in order, wrapping round. The glyph keeps rumors
  apart without colour. *(My call.)*
- The mark is assigned on the page from the order of the player's whispers, so it is stable across
  reloads (the replay and `/api/state` give the order).
- Replies in a conversation belong to the rumor of the whisper that opened it.

## Mobile

Below 760px the three columns become three tabs (Rumors, Hear, Village); the dock and box stay at
the bottom. *(My call.)*

## What the server adds

- Nothing new in the game's rules. The page needs, per conversation, the villager, its state and its
  lines — the events already carry these (`whisper_status`, `reply`, `asked`); if a gap appears, the
  plan adds the smallest field to an existing event.

## Left to the builder

- Exact colours (accessible in light and dark), spacing, the expansion's layout, the dock's chat
  height, the quiet line wording, and how the feed handles a burst of quiet lines (folding repeats).
