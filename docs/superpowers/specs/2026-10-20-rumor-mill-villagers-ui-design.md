# Rumor Mill: Finding Marks — Design

**Status:** proposed
**Author:** design brainstorm, 2026-10-20
**Builds on:** the shipped game (PRs #473-#476): the web page, shrug hints, suspicion.

## Intent

The first real playtest broke on the game's core loop. A player read a wine
merchant's brush-off ("half my sources have gone silent on their yields"),
whispered that the harvest was in danger, and was brushed off again, then waited
about a minute per whisper to find out. Meanwhile the villager sidebar was a
hundred names in a column. Finding a mark, whispering, and knowing what happened
has to feel fair, fast, and legible, or the game is dead.

This changes three things:

1. **Matching** decides "does this whisper touch what this villager cares about"
   per need, not with one global similarity cutoff.
2. **Feedback** is immediate. The player learns at once whether a whisper landed,
   and the shrug timer goes away.
3. **The village panel** becomes places as tiles of generated pixel faces, with a
   status badge per villager, replacing the long list.

## Goals

- A whisper that obviously fits a villager's concerns lands; an unrelated one
  doesn't. The playtest case (a harvest-and-pestilence whisper to a villager who
  fears a curse on her vines) must land.
- The player knows within about a second whether a whisper landed, and if the
  villager is busy, how long until they act on it.
- The village fits in the sidebar without scrolling a hundred names, is
  pleasant to look at, and makes the player's knowledge about each villager
  visible at a glance.
- Accessible: nothing the player needs is carried by color alone; the faces are
  decoration.

## Non-goals

- A map, avatars that move, or portraits drawn by a model.
- Changes to Contex itself (`src/`).
- New mechanics. This is matching, feedback, and presentation.

## Anti-goals

- Revealing villagers' needs, or a numeric relevance score, to the player.
- A model call to decide whether a whisper lands. Matching stays embedding-based
  and costs no tokens.
- Losing "rumors wake only by meaning." Matching gets better, not bypassed.

## Why the current matching fails (measured)

Contex's embedding model (gte-base) puts almost every whisper-to-need similarity
in a narrow band, about 0.74-0.81, related or not. Against Beatrix Vintner's
needs in the running game:

| Need | Harvest/pestilence whisper | Haunted-tavern whisper | Unrelated (apprentice burned his hand) |
|---|---|---|---|
| "I fear the old woman's curse on my vines..." | 0.807 | 0.775 | 0.769 |
| "which vineyards are producing this year..." | 0.780 | 0.756 | 0.742 |
| "whether I can expand my cellar..." | 0.791 | 0.799 | 0.784 |
| "my husband's former mistress..." | 0.783 | 0.786 | 0.784 |

The relevant whisper ranks first for the needs it fits, but by 0.02-0.04, and
each need has its own baseline: the cellar need scores everything high. A single
cutoff (0.815) cannot separate them. The "The newcomer whispered to you..."
framing barely moves the numbers (measured with and without it).

## Matching: per-need margin

- **Baseline per need.** Each need's baseline is its mean similarity against a
  fixed calibration set of about 20 ordinary village sentences (weather, chores,
  market prices, a lost goat) that concern nobody in particular. The set lives in
  the code. Baselines are computed once per need by publishing the set to a
  calibration project and querying each need against it, and cached in a sidecar
  file next to the seed, keyed by the need text, so later runs skip the work.
- **A match is a margin.** A whisper or retelling matches a need when its
  similarity exceeds that need's baseline by at least `match_margin` (Config,
  starting value 0.03, tuned against real whispers). From the table: the harvest
  whisper clears the curse need by about 0.035 and the vineyard need by about
  0.04; the tavern whisper does not clear the cellar need (about 0.015).
- **One subscription per need.** *(My call, after a builder read.)* Each villager
  holds one subscription per need, with that need's threshold set to its baseline
  plus `match_margin`. Contex applies the threshold per subscription server-side, so
  only real matches enter a bundle and push. This avoids two problems a single
  low-threshold subscription would have: a push for nearly every publish into every
  mind (reconcile load across a hundred minds), and, with hybrid search on (the
  docker-compose default), low-relevance chatter that shares words with a need
  outranking a real match in the top-k bundle. `Mind` listens on all of a
  villager's subscription URIs in one listen call and merges their bundles.
- **Similarity reaches the game.** Contex bundles and query results carry each
  match's similarity; `Match` gains a `similarity` field so the game can use it.
- **Calibration shape.** Calibration sentences are published in the same shape as
  utterances (`Utterance.to_data`), because Contex embeds the whole payload and the
  shared fields shift every score; baselines are only comparable in that shape.
  Calibration queries pass `threshold=0` and a `top_k` covering the whole set.
- **The judge** calibrates each goal's evidence text the same way (a few baselines
  per session) and uses the same margin rule in place of its fixed threshold.

## Feedback: no shrug timer

Contex signals a match only by pushing when a subscription's bundle changes.
After a publish, subscriptions are re-matched in the background, so a miss
produces nothing at all, and the game used to infer a miss from silence (the
50-second shrug timer). Instead:

- **Direct check.** Right after delivering a whisper, the game asks Contex
  whether it matches any of the target's needs: one query per need on the
  target's mind (3-5 queries, no model call), applying the margin rule. This
  depends on the published item being searchable when the publish returns; the
  builder confirms that before relying on it.
- **Miss:** the brush-off line (existing shrug hint) is written and shown at
  once. No timer.
- **Match:** the whisper is marked heard and added to the villager's next turn
  through the engine's pending path (the same one addressed messages use), so the
  turn sees it even if a push is slow or the whisper is not in the top of the
  bundle. The decision was made by meaning; this only guarantees delivery. The
  check queries with `since` set to the publish time so the whisper is not
  outranked by older memories in the query's `top_k`.
- **Miss:** the chip shows "Brushed you off." at once; the hint line is appended
  when Haiku returns it.
- **What happened next** is reported on the whisper as it resolves. A status can
  move forward: "kept to herself" (her first turn after hearing it did not relay it)
  becomes "took the bait" if she later speaks any utterance whose lineage root is
  this whisper. "Warned others about you" is shown if her turn after hearing it was a
  warning.

## Whisper status chip

Each whisper in the feed carries one status, updated in place:

| State | Shown |
|---|---|
| checking | a static "…" (sub-second) |
| busy | "Beatrix is busy. She'll hear you in about 18s." A countdown in seconds, updated once a second without animation. |
| heard | "Beatrix heard you." |
| took the bait | "Took the bait. Told 3 people." (count of listeners in her first relay) |
| kept to herself | "Heard you, kept it to herself." |
| warned others | "Heard you, and warned others about you." |
| brushed off | "Brushed you off:" plus the hint line when it arrives |
| turned away | "Turned you away:" plus the refusal line |

The engine exposes, per villager, the time their cooldown ends, so the page can
show the countdown. States change through feed events (`whisper_status`, with the
whisper id, villager id, state, and the extra fields above), so reloads replay
them.

## Village panel: places as tiles

- **Tiles.** One tile per place (square, tavern, market, well, mill, chapel,
  gate), each with the count of villagers there and their faces, wrapping.
  A tile can be opened to a list of its villagers: face, name, trade, status
  badge, and the player's note (the latest brush-off hint). One tile is open at
  a time; the player's tile choice is remembered for the session.
- **Find.** One search box above the tiles filters by name, trade, or place,
  highlighting matching faces and opening the place that holds the best match.
  The whisper "To" box offers the same matches as a typeahead, with face, trade,
  place, and note.
- **Card.** Clicking a face opens the villager's card (occupation, personality,
  grudge, relationships, the player's notes, never needs) in a popover beside the
  tile, with a "Whisper to <name>" button that fills the To box and focuses the
  message.
- **Live.** Moves, departures, and arrivals update the tiles without re-sorting
  under the pointer or losing keyboard focus (the existing rule for the list).

## Pixel faces

- **Generated, deterministic.** A 12x12 head-and-shoulders drawn on a canvas from
  the villager's id (skin, hair, hair style, beard or not, background tint) and
  trade (clothes color; a hood for clergy, an apron mark for smiths). No images
  are stored; the same villager has the same face every run.
- **Variety.** Six skin tones, seven hair colors, four hair styles; drawn from the
  id, never the name.
- **Decoration.** The canvas is `aria-hidden`; the name beside it is what a screen
  reader reads, followed by the status in words.
- **Edges.** A thin outline keeps faces visible on both themes.
- **Status badge.** A small corner badge on the face: ✓ took the bait, – brushed
  you off, ! wary of you (turned you away or warned others), … still being heard.
  Shape and a word carry the meaning; color is secondary.

## The player's notes

The server keeps, per villager, what the player has learned: their current status
toward the player (from the whisper states above) and the latest brush-off hint.
It is included in `/api/state` and the villager card so a reload restores it. It
contains only what the feed already showed the player.

## Constraints the game documents

- Contex runs with auth off (rate limits would throttle calibration and the direct
  check) and with `SYSTEM_ONE_ENABLED` off (query reranking would call a model on
  every direct check). The README says both; the game logs a warning at startup if a
  query result carries a rerank marker.

## Left to the builder

- The calibration sentences and the sidecar cache format.
- Exact layout, sizes, and copy within the page's existing design language.
- The countdown's update mechanism.
- Tests: the margin rule with the measured numbers above as a fixture (harvest
  lands on the curse need, tavern does not land on the cellar need); the direct
  check deciding hit and miss without a timer; status transitions; the face
  generator is deterministic and varied; no needs or scores in any response.
