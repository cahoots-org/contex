# Rumor Mill: Following, Overhearing, and Talking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suspicion follows what villagers can know, stories wear out, the player's rumors read as cards apart from filtered village talk, villagers remember what they said, and an interested villager can hold a short conversation with the player.

**Architecture:** Per-utterance facts (depth, names_newcomer, audience, action, style) live in engine memory keyed by utterance id. `Mind.poll` reports each match's clearance over its need's threshold; the engine applies fatigue by depth and an overheard margin. A per-session `rumors` project and in-memory per-whisper baselines decide whether a hop is on topic; a per-session `goals` project decides goal relevance for overhearing. Each villager gets a `said` project. A new `answer` action replies to the player; replies are threaded onto the first whisper's card.

**Tech Stack:** The Rumor Mill as in PR #477.

**Spec:** `docs/superpowers/specs/2026-10-21-rumor-mill-talk-design.md` (read all of it, including "Decisions after a builder read").

**Branch:** `feat/rumor-mill-talk`, cut from `feat/rumor-mill-tiles` (PR #477, open). Rob merges; do not merge.

## Global Constraints

- Everything under `examples/rumor_mill/`; **no changes under `src/`**.
- Python 3.12; imports at the top; short docstrings; comments say what code does; no inline imports; no meta/history comments; no gnarly cleverness.
- No model call to decide what the player sees, whether a story wears out, or whether a hop is on topic. Matching stays embedding-based.
- The published utterance payload stays `id` and `text` only.
- Never send needs, similarities, clearances, baselines, margins, or suspicion numbers to the terminal, browser, or logs above DEBUG.
- Page text from villagers, the player, or a model goes in through `textContent` or text nodes only.
- Config fields with `RUMOR_MILL_*` overrides, starting values tuned in Task 9: `fatigue_per_hop=0.01`, `overheard_margin=0.01`, `on_topic_margin=0.02`, `overhear_rate=0.125`, `said_floor=0.8`, `talk_turns=4`.
- Each commit passes `ruff check .` and `pytest examples/rumor_mill`; one commit per task; imperative subject; body says why; ends with the session's attribution line.

## Review Focus

1. A newcomer line that lands but the villager's turn fails or picks another action: the conversation chip ends in a final state, never stays `answered` forever (Task 7).
2. A turn whose new items are all stale must not run, and the stale items must still be in the villager's recent memories (Task 3).
3. A retelling in any non-faithful style never names the newcomer and never raises suspicion; a faithful retelling of a whisper does (Task 2).
4. A reload after more than 500 feed events still shows every rumor card with its hops and conversation (Task 4, Task 8).
5. Overheard lines come only from the player's place, and nothing on a rumor card is repeated as overheard (Task 5).

---

### Task 1: Utterance facts and per-need clearance

**Files:** Modify `models.py` (Utterance gains `depth`, `names_newcomer`, `audience`, `action`, `style` with defaults; `to_data` unchanged), `lineage.py` or a new `facts.py` (an in-memory `Facts` store keyed by utterance id with `record(utterance)` and `get(uid)`), `mind.py`, `engine.py`, `player.py`, `effects.py`; tests.

**Interfaces:**
- `Match` gains `clearance: float | None = None`.
- `Mind.poll()` sets each new match's clearance to its best (similarity − that need's threshold) across the needs whose bundle holds it.
- `Player` direct hits pass clearance over the matching need's threshold to `hear_directly`.
- `effects.act` fills `depth` (source depth + 1, or 0), `audience` (`"one"` for retell/deny/confront/warn, `"room"` for speak/preach/announcements), `action`, `style` on every utterance and records it in `Facts` before delivery. Whispers are depth 0, audience `"one"`.

- [ ] Tests first: `test_poll_reports_best_clearance_across_needs`, `test_direct_hit_carries_clearance`, `test_act_records_depth_audience_action_style`, `test_payload_unchanged`.
- [ ] Run `pytest examples/rumor_mill -v` — FAIL; implement; PASS; `ruff check .`.
- [ ] Commit `Record how each line was said and how well it matched`.

### Task 2: Suspicion follows knowledge

**Files:** Modify `writer.py`, `effects.py`, `decide.py`, `engine.py`; tests.

**Interfaces:**
- `names_newcomer` is set by `effects.act` for every speech action with a source: true only when the source is a whisper or a line that named the newcomer, and the style is `faithful`.
- `Writer.say(...)` receives the source text without the "The newcomer whispered to you at …" framing and an instruction not to say who started it, unless `names_newcomer` is true.
- `TurnView.player_rooted` is replaced by `names_newcomer` items (new items whose `Facts` say they name the newcomer, plus the player's own whispers). The suspicion Score question is asked only when that list is non-empty; its instructions drop the "sensational or hurtful to someone they know" clause.

- [ ] Tests first: `test_non_faithful_retelling_drops_the_newcomer` (flag false, prompt has no framing, Review Focus 3), `test_faithful_retelling_of_a_whisper_names_the_newcomer`, `test_shift_blame_drops_the_newcomer`, `test_score_question_only_when_a_line_names_the_newcomer`, `test_hurtful_gossip_raises_no_suspicion`.
- [ ] RED, implement, GREEN, ruff.
- [ ] Commit `Suspect the newcomer only for what a villager could know`.

### Task 3: Stories wear out

**Files:** Modify `engine.py`; tests.

**Interfaces:**
- In `_turn`, an item is fresh when its clearance ≥ `fatigue_per_hop × depth` + (`overheard_margin` if its audience is `"room"`). Items with no clearance (addressed warnings, the player's whispers) are fresh.
- Only fresh items go to `new`; the rest join `recent`. No turn runs without a fresh item; the stale keys are still consumed (not re-heard).

- [ ] Tests first: `test_deep_retelling_does_not_wake`, `test_room_talk_needs_the_overheard_margin`, `test_stale_items_become_memories` (Review Focus 2), `test_no_turn_without_a_fresh_item`, `test_warnings_always_fresh`.
- [ ] RED, implement, GREEN, ruff.
- [ ] Commit `Let a story wear out as it is retold`.

### Task 4: Rumor cards: on topic, tallies, and state

**Files:** Create `rumors.py`; modify `engine.py` (deliver awaits the topic check), `feed.py`, `player.py` (publish each whisper's words into the `rumors` project and calibrate its baseline in memory), `calibration.py` (an in-memory, non-persisted calibrate call), `web.py` (`/api/state` gains `rumors`); tests.

**Interfaces:**
- `Rumors(contex, baselines, config)`: `async add(whisper)`; `async on_topic(root_id, text) -> bool` (query `rumor-mill:<session>:rumors` with the hop's words, filtered to the root's key, `since` the whisper's publish time, `threshold=0`; on topic when similarity ≥ the whisper's baseline + `on_topic_margin`); per card: hop ids on topic, `heard` (distinct listeners of on-topic hops), `still` (listeners of on-topic hops in the last game day), `drifted` (bool, set when a descendant turns off topic); `cards() -> list[dict]` for `/api/state`.
- `Engine.deliver`, for an utterance whose lineage root is a whisper, awaits `on_topic` before emitting; the feed emits `hop` only for on-topic hops and a `drift` event once per card when the first off-topic descendant appears.
- `_report` counts "took the bait" only for an on-topic relay.

- [ ] Tests first: `test_off_topic_descendant_is_not_a_hop_and_marks_drift`, `test_took_the_bait_needs_an_on_topic_relay`, `test_tallies`, `test_state_carries_cards_beyond_the_replay_window` (Review Focus 4), `test_whisper_baseline_is_not_cached_to_disk`.
- [ ] RED, implement, GREEN, ruff; gated live test that an on-topic paraphrase passes and an off-topic reply fails.
- [ ] Commit `Keep a rumor's card to the lines still carrying it`.

### Task 5: Overhearing at the player's place

**Files:** Create `overhear.py`; modify `player.py` (the player's place), `engine.py`, `feed.py`, `__main__.py` (publish goal evidence to `rumor-mill:<session>:goals`), `web.py` (`place` in state); tests.

**Interfaces:**
- `Player.place` is the place of the last whisper target; `"square"` at start; emitted as a `place` event and in `/api/state`.
- `Overhear(contex, baselines, config, rng)`: `async keep(utterance) -> str | None` returns a reason (`inflammatory`, `goal`, `random`) or None for an utterance spoken at the player's place that is not a hop on a card. Inflammatory: action `confront`/`deny` or style `exaggerated`/`mock`/`shift_blame`. Goal: a query of the line's words against the goals project clears that goal's threshold. Random: `rng.random() < overhear_rate`.
- The feed emits `overheard` (speaker, speaker_name, place, text, reason, uid) for kept lines; the terminal prints nothing new.

- [ ] Tests first: `test_only_the_players_place`, `test_card_hops_are_not_overheard` (Review Focus 5), `test_inflammatory_kept`, `test_goal_relevant_kept`, `test_random_rate`, `test_place_follows_the_last_whisper`.
- [ ] RED, implement, GREEN, ruff.
- [ ] Commit `Let the player overhear some of what is said around them`.

### Task 6: Villagers remember what they said

**Files:** Modify `engine.py` (publish every spoken line to `rumor-mill:<session>:said:<vid>`), `decide.py` (state gains `things_i_said`), `writer.py` (`say` and the new reply see them); tests.

**Interfaces:**
- `Engine` publishes each utterance a villager speaks to their said project (key `said:<uid>`, payload `id`/`text`). On a turn whose new items include a newcomer line, it queries the villager's said project with that line (`top_k=3`, `max_age_seconds=memory_seconds`) and keeps matches with similarity ≥ `said_floor`.

- [ ] Tests first: `test_spoken_lines_go_to_the_said_project`, `test_newcomer_line_retrieves_related_said_lines`, `test_said_lines_reach_the_jev_state`, `test_said_project_has_no_subscription`.
- [ ] RED, implement, GREEN, ruff.
- [ ] Commit `Remember what each villager has said`.

### Task 7: Conversations

**Files:** Modify `actions.py` (`ANSWER`), `decide.py`, `effects.py`, `writer.py` (`reply`), `player.py` (conversation state, threading), `feed.py` (`reply` events, `answered`/`moved_on` states), `notes.py` (rank), `web.py`; tests.

**Interfaces:**
- `ANSWER` offered only when the turn's new items include a newcomer line that landed and the conversation has fewer than `talk_turns` replies; target is the newcomer.
- `effects.act` for `ANSWER`: `Writer.reply(villager, newcomer_line, things_i_said)` writes one short line; records it in lineage (parent = the newcomer line) and the said project; emits `reply` (conversation id, villager id/name, text, uid); no mind delivery.
- `Player` keeps `conversation_with[vid] = first_whisper_id`; a whisper to that villager while it is open is a reply: its own root, depth 0, tagged with the conversation id. Whisper status gains `answered` (non-final) and `moved_on` (final). A landed reply whose turn picks another action, a failed turn, or the cap reached emits `moved_on` (Review Focus 1). A miss ends it with the brush-off as now.
- `notes.py` RANK and the page's RANK gain `answered` between listening and kept.

- [ ] Tests first: `test_answer_offered_only_after_a_landed_newcomer_line`, `test_answer_emits_a_reply_and_records_it`, `test_reply_threads_onto_the_first_whisper`, `test_cap_ends_with_moved_on`, `test_other_action_ends_with_moved_on`, `test_failed_turn_ends_with_moved_on`, `test_notes_rank_answered`.
- [ ] RED, implement, GREEN, ruff.
- [ ] Commit `Let an interested villager talk back`.

### Task 8: The page: rumor cards, village talk, conversations

**Files:** Modify `static/index.html`, `README.md`; `web.py` only if a field is missing (with a test).

- Rumor cards (approved mockup A at `.superpowers/brainstorm/69755-1792582317/content/rumors-vs-chatter.html`): title is the whisper, status chip, tally "heard by N · still told by M", on-topic hops threaded, a drift line, and the conversation (newcomer lines and replies alternating) when there is one; the composer shows "Talking with <name>" while a conversation is open.
- Village talk blocks between cards: muted, per place, count plus two newest lines, expandable; each line has "Talk to <name> about this" (fills To, quotes the line in the message box).
- Warnings, refusals, mood and goal changes stay full-strength outside cards.
- "You're at <place>" in the goals strip.
- Cards restored from `/api/state` on reload (Review Focus 4).
- Build first without a browser; then a separate browser verification step (as Task 7 of the previous plan did: stub server, short bounded steps, desktop and phone, light and dark).
- [ ] Commit `Show your rumors apart from the village's talk`.

### Task 9: Play it and tune it

- [ ] Replay the playtest whisper ("I heard there was some strange goings on at the tavern. I think it may be haunted." to a villager at the tavern) with the 100-villager seed: the rumor spreads a few on-topic hops, the tavern settles within a couple of minutes, one whisper does not get the player run out, the feed shows the card plus a manageable talk block.
- [ ] Hold a conversation with an interested villager for a few turns; bring up something they said in overheard talk and confirm they connect it.
- [ ] Tune the six Config values; report numbers (hops per whisper, lines per minute at the tavern, suspicion after one whisper, overheard lines per minute, cost per 5 minutes).
- [ ] Commit tuning per module, then `Document following, overhearing, and talking`; leave the game running for Rob with stop commands. Do not push.

---

## Self-Review

- **Spec coverage:** knowledge-based suspicion (2), fatigue and overheard margin with clearance (1, 3), cards with on-topic hops, drift, tallies, state (4), player place and overhearing (5), said-memory (6), conversations with threading and chip states (7), page (8), tuning (9). Decisions section items are each placed.
- **Interfaces:** `Facts`, `Match.clearance`, `Rumors.on_topic`, `Overhear.keep`, `ANSWER`, `Writer.reply`, `answered`/`moved_on` are named consistently.
