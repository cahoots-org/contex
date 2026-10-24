# Rumor Mill: Finding Marks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Whispers that fit a villager's worries land, the player learns within about a second whether they did, and the village panel becomes place tiles of generated pixel faces with status badges.

**Architecture:** Each need gets a baseline (mean similarity against a calibration set, cached beside the seed) and its own Contex subscription at baseline + margin, so only real matches push. A whisper is checked directly against the target's needs right after publishing; a hit goes to the villager's next turn through the engine's pending path, a miss is brushed off at once. Whisper status and per-villager notes travel as feed events and live in `/api/state`. The page renders tiles, faces, search, a card popover, and status chips.

**Tech Stack:** The shipped Rumor Mill (Python 3.12, FastAPI, `mcp`, `typesafe-sdk`, `anthropic`), canvas in the page, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-20-rumor-mill-villagers-ui-design.md`. Read it first.

**Branch:** `feat/rumor-mill-tiles` (already cut from `feat/rumor-mill-suspicion`, PR #476; the spec commit is on it). Rob merges; do not merge.

## Global Constraints

- Everything under `examples/rumor_mill/`; **no changes under `src/`**.
- Python 3.12; imports at the top; short docstrings; comments say what code does; no inline imports; no meta/history comments; no gnarly cleverness.
- Matching never calls a model. Never send a villager's needs or any similarity, baseline, margin, or suspicion number to the terminal, the browser, or a log line above DEBUG.
- Page text that comes from villagers, the player, or a model goes in through `textContent` or text nodes only.
- Nothing the player needs is carried by color alone; faces are `aria-hidden` decoration.
- Config fields (with `RUMOR_MILL_*` overrides), starting values tuned in Task 8: `match_margin=0.03`, `calibration_path` (default: the seed path with `.baselines.json` appended). The old `threshold` field is removed; `judge_threshold` is removed.
- Each commit passes `ruff check .` and `pytest examples/rumor_mill`; one commit per task, imperative subject, body says why, ending with the attribution line the session specifies.

## Review Focus

1. A villager whose needs change (a newcomer, a reload with a different seed) gets baselines computed, not a stale cache entry (Task 2).
2. A whisper whose direct check errors (Contex down) is reported as not carried, never as a hit or a miss, and leaves no pending entry (Task 4).
3. A whisper to a villager who leaves between the check and their turn leaves no pending entry and no stuck status chip (Task 4).
4. The playtest fixture: the harvest/pestilence whisper clears Beatrix Vintner's curse need, the haunted-tavern whisper does not clear her cellar need, the unrelated whisper clears none (Task 2).
5. Status never moves backwards (took the bait never becomes kept to herself), and a reload replays to the same statuses (Task 5).

---

### Task 1: Similarity on matches, and `since` on queries

**Files:**
- Modify: `examples/rumor_mill/contex_client.py`, `examples/rumor_mill/tests/test_contex_client.py`, `examples/rumor_mill/tests/test_contex_integration.py`

**Interfaces:**
- Produces: `Match(data_key: str, data: dict, similarity: float | None = None)` (frozen; existing positional callers keep working); `read()` and `query()` fill `similarity` from the server's `similarity` field; `query(..., since: str | None = None)` passes `since` (ISO-8601) only when set; `listen_many(uris: list[str]) -> AsyncIterator[None]` (one listen session over several resource URIs, one yield per push, plus the catch-up yield on connect, like `listen`).

- [ ] **Step 1: Write the failing tests:** `test_read_keeps_similarity`, `test_query_keeps_similarity_and_sends_since_only_when_set`, `test_listen_many_yields_on_connect_and_per_push` (stubbed session); gated live test `test_query_since_excludes_older_items`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_contex_client.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** `listen_many` uses the same MCP `listen(resource_subscriptions=[...])` call as `listen`.
- [ ] **Step 4: Run** the full suite and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Keep match similarity and let queries look back only so far`.

---

### Task 2: Need baselines and the margin rule

**Files:**
- Create: `examples/rumor_mill/calibration.py`, `examples/rumor_mill/tests/test_calibration.py`
- Modify: `examples/rumor_mill/config.py`, `examples/rumor_mill/tests/test_config.py`

**Interfaces:**
- Produces:
  - `CALIBRATION: tuple[str, ...]` (about 20 ordinary village sentences that concern nobody).
  - `Baselines(contex, config)`: `async load(texts: list[str]) -> None` computes missing baselines (publish the calibration set once per session into `rumor-mill:<session>:calibration` in `Utterance.to_data` shape, then one `query(project, text, top_k=len(CALIBRATION), threshold=0)` per text; baseline = mean similarity), reads and writes the JSON cache at `config.calibration_path` keyed by the exact text; `get(text) -> float`; `threshold(text) -> float` = `get(text) + config.match_margin`; `matches(text, similarity) -> bool` (similarity at or above `threshold(text)`).
  - Calibration runs with bounded concurrency (8) and logs one INFO line with the count computed versus cached, nothing else.

- [ ] **Step 1: Write the failing tests:** `test_baseline_is_mean_of_calibration_scores`, `test_cache_hit_skips_queries`, `test_new_text_is_computed_even_with_a_cache` (Review Focus 1), `test_corrupt_cache_is_ignored_and_rewritten`, `test_threshold_adds_margin`, `test_playtest_fixture` (Review Focus 4: with baselines set from the measured unrelated scores, the harvest score 0.807 clears the curse need, the tavern score 0.799 does not clear the cellar need whose baseline sits at 0.784). Config: `match_margin` default and override; `threshold`/`judge_threshold` gone.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_calibration.py examples/rumor_mill/tests/test_config.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Fixture values in the test come from the spec's table, not from a live run.
- [ ] **Step 4: Run** the full suite and `ruff check .` — Expected: PASS (fix any callers of the removed Config fields with the smallest change; Task 3 and Task 6 replace them).
- [ ] **Step 5: Commit** `Calibrate each need against ordinary village talk`.

---

### Task 3: One subscription per need

**Files:**
- Modify: `examples/rumor_mill/mind.py`, `examples/rumor_mill/engine.py` (construct minds with baselines), `examples/rumor_mill/__main__.py` (load baselines for every villager's needs before adding villagers; newcomers' needs are loaded before they join), `examples/rumor_mill/population.py`, tests for each

**Interfaces:**
- Consumes: Task 1 `listen_many`, `Match.similarity`; Task 2 `Baselines.threshold`.
- Produces: `Mind(contex, villager, config, baselines)`; `open()` subscribes once per need with `needs=[need]` and `threshold=baselines.threshold(need)`, keeping `uris: list[str]`; `_read()` reads every URI (resubscribing any that expired) and merges into one bundle keyed by need; `poll`, `renew`, `matches` keep their meanings over the merged bundle; `pushes()` listens on all URIs with `listen_many`. `mind_factory` and the engine pass `baselines`.

- [ ] **Step 1: Write the failing tests:** `test_open_subscribes_once_per_need_with_its_threshold`, `test_poll_merges_bundles_and_dedupes_keys`, `test_one_expired_subscription_is_recreated_alone`, `test_pushes_listens_on_all_uris`; engine and population tests updated for the new constructor.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_mind.py examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_population.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite, the gated live tests against a local Contex if one is up, and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Give each need its own subscription and threshold`.

---

### Task 4: Check a whisper directly, drop the shrug timer

**Files:**
- Modify: `examples/rumor_mill/player.py`, `examples/rumor_mill/engine.py`, `examples/rumor_mill/feed.py`, tests for each

**Interfaces:**
- Consumes: Task 1 `query(since=...)`, Task 2 `Baselines.matches`, Task 2/3 wiring; the engine's `_pending` and `_wakes` from addressed delivery.
- Produces:
  - `Engine.cooldown_ends(vid) -> float` (wall-clock epoch seconds when the villager can next take a turn; now or earlier when free) and `Engine.hear_directly(vid, match: Match) -> None` (adds to `_pending[vid]` and sets the wake; no publish; ignored for a departed villager).
  - `Player.whisper_to`: after a successful delivery (existing refusal and game-over checks first), record the publish time and run `check(target, utterance) -> bool | None`: for each need, `contex.query(mind_project, need, top_k=config.top_k, since=published_at)` and look for the whisper's key with `baselines.matches(need, similarity)`. A hit: `engine.hear_directly(...)`, then the `heard` or `busy` status. A miss: the `brushed_off` status at once, then the brush-off line from `Writer.brush_off` added to the same whisper's status when it returns (same need-cycling and echo guard as now). An error from the check: the whisper reply is "The whisper didn't carry; try again.", no status, no pending (Review Focus 2).
  - The shrug timer, `_shrugs`, `shrug_seconds`, and `note_heard`'s cancellation role are removed (keep `note_heard` only if Task 5 needs it).
  - `Feed.on_whisper_status(whisper_id, villager_id, name, state, **extra)` emits kind `whisper_status` with `state` in `checking`, `busy`, `heard`, `brushed_off` (with optional `hint`) and the extra fields `until` (epoch seconds, `busy` only). The terminal sink prints only `brushed_off` (`<Name> brushes you off: "<hint>"` or `<Name> shrugs it off.`) as today and nothing for the other states.

- [ ] **Step 1: Write the failing tests:** `test_hit_goes_to_the_next_turn_without_a_push`, `test_miss_is_brushed_off_at_once_then_gets_its_hint`, `test_busy_villager_reports_until`, `test_check_error_reports_not_carried_and_leaves_no_pending` (Review Focus 2), `test_target_leaving_before_the_turn_leaves_no_pending` (Review Focus 3), `test_check_uses_since_and_each_needs_threshold`, `test_terminal_prints_only_the_brush_off`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_feed.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Tell the player at once whether a whisper landed`.

---

### Task 5: What happened next, and the player's notes

**Files:**
- Create: `examples/rumor_mill/notes.py`, `examples/rumor_mill/tests/test_notes.py`
- Modify: `examples/rumor_mill/engine.py` (report the hearer's turn outcome), `examples/rumor_mill/feed.py`, `examples/rumor_mill/web.py` (`/api/state` and the card carry notes), tests

**Interfaces:**
- Consumes: Task 4 statuses; `Lineage.root`, `Lineage.is_player_rooted`; `Decision.action`.
- Produces:
  - `Notes(feed)`: per villager `status` (one of `none`, `listening`, `took_the_bait`, `kept`, `brushed_off`, `wary`) and `hint` (latest brush-off line). Status ranks `took_the_bait` > `wary` > `kept` > `brushed_off` > `listening` > `none`; an update never lowers it, except `wary` (refusal or warning) overrides anything (Review Focus 5). Updates come from feed events, so a reload replays to the same notes.
  - `Engine` after a turn whose new items included a player-rooted whisper: emits `whisper_status` `took_the_bait` (with `told`, the number of listeners) when the turn relayed an utterance rooted at that whisper, `warned` when the action was `warn`, otherwise `kept`. A later relay rooted at that whisper by the same villager upgrades `kept` to `took_the_bait`.
  - `/api/state` gains `notes: {villager_id: {status, hint}}`; the card endpoint includes the villager's note. Nothing else.

- [ ] **Step 1: Write the failing tests:** `test_status_never_moves_backwards`, `test_wary_overrides`, `test_replay_rebuilds_the_same_notes`, `test_turn_that_relays_reports_took_the_bait_with_count`, `test_turn_that_ignores_reports_kept_then_later_relay_upgrades`, `test_state_and_card_carry_notes_without_needs_or_numbers`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_notes.py examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_web.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Report what each villager did with your whisper`.

---

### Task 6: The judge uses the margin rule

**Files:**
- Modify: `examples/rumor_mill/judge.py`, `examples/rumor_mill/__main__.py` (calibrate the dealt goals' evidence), `examples/rumor_mill/tests/test_judge.py`

**Interfaces:**
- Consumes: Task 2 `Baselines`.
- Produces: `Judge(..., baselines)`; `_believes` queries with `threshold=baselines.threshold(goal.evidence[0])` in place of `judge_threshold`; group-record queries the same way.

- [ ] **Step 1: Write the failing tests:** `test_judge_queries_with_the_evidence_threshold`, `test_villager_below_the_margin_costs_no_jev_call`.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_judge.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Judge belief with the same margin as hearing`.

---

### Task 7: Place tiles, pixel faces, search, card, and status chips

**Files:**
- Modify: `examples/rumor_mill/static/index.html`, `examples/rumor_mill/web.py` (only if the page needs a field it lacks), `examples/rumor_mill/README.md`

**Interfaces:**
- Consumes: `/api/state` (`villagers` with id, name, occupation, place; `places`; `notes`), events `villagers`, `whisper_status`, existing kinds.
- Produces (page only):
  - **Faces:** `faceFor(id, occupation)` draws the 12x12 face from the spec on a canvas (same algorithm as the approved mockup in `.superpowers/brainstorm/58792-1792523960/content/portraits-a11y.html`), `aria-hidden`, outlined, cached per id.
  - **Tiles:** one per place with a count and faces; one open tile at a time listing face, name, trade, badge, and hint; the open tile is remembered in `localStorage` (wrapped in try/catch).
  - **Badges:** ✓ took the bait, – brushed you off, ! wary, … listening; with the status in words for screen readers.
  - **Find:** one search box filtering by name, trade, or place; the To box typeahead with face, trade, place, and hint; keyboard operable.
  - **Card:** a popover beside the tile with occupation, personality, grudge, relationships, note, and "Whisper to <name>" filling the To box and focusing the message; Escape closes it; focus returns to the face.
  - **Chips:** each whisper in the feed shows its status; `busy` counts down to `until` once a second without animation; `brushed_off` shows the hint when it arrives.
  - **Live updates** keep the existing rule: no re-sort under the pointer, focus kept.
  - README: the village panel, statuses, and badges in plain words.

- [ ] **Step 1: Build the page** against a stub server (as earlier web work did) with 100 generated villagers and scripted events.
- [ ] **Step 2: Verify in a real browser** at desktop and phone width, light and dark: tiles fit without a 100-name scroll; search and typeahead find a villager by trade; card opens and fills the To box; each status renders with its badge and words; a reload restores statuses and notes; keyboard path works; no console errors. Screenshots in the scratchpad.
- [ ] **Step 3: Run** `pytest examples/rumor_mill` and `ruff check .` — Expected: PASS.
- [ ] **Step 4: Commit** `Show the village as places of faces`.

---

### Task 8: Play it with real models and tune the margin

**Files:**
- Modify: the module that owns a tuned value; `examples/rumor_mill/README.md` for final defaults

- [ ] **Step 1: Reproduce the playtest:** with the 100-villager seed, whisper "Did you know the harvest is in danger? There's a pestilence about" to Beatrix Vintner: it lands; "Did you know people think the tavern is haunted? Maybe that's related to your shipments" does not. Report the scores against her baselines.
- [ ] **Step 2: Tune `match_margin`** over at least 20 whispers you write before looking at needs (half aimed at a villager's evident trade and grudge, half unrelated): report hit rate on aimed and false hits on unrelated; pick the margin that lands most aimed whispers with few false hits. Confirm the time from whisper to status is about a second for a miss and the turn follows a hit.
- [ ] **Step 3: Check cost and load:** subscriptions per mind, push volume per publish, calibration time at startup (first run and cached), judge Jev calls per check before and after.
- [ ] **Step 4: Run** `pytest examples/rumor_mill -v && ruff check .`, commit each tuning change with its module, then `Document the matching defaults`.
- [ ] **Step 5:** Leave the updated game running for Rob with exact stop commands. Do not push.

---

## Self-Review

- **Spec coverage:** per-need margin and baselines (2), per-need subscriptions (3), similarity on matches and `since` (1), direct check replacing the timer, hit through pending, immediate miss with hint appended (4), status table and progression, notes (5), judge margin (6), tiles, faces, badges, search, card, chips, live updates (7), constraints (Global), tuning against the playtest case (8).
- **Interfaces:** `Match.similarity`, `Baselines.threshold/matches`, `Mind(..., baselines)`, `Engine.hear_directly/cooldown_ends`, `Feed.on_whisper_status`, `Notes` are named the same everywhere they recur.
- **Proportion:** interfaces, rules, and test names only.
