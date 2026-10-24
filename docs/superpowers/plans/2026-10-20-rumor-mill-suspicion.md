# Rumor Mill: Suspicion and Losing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The player can lose: villagers who suspect the newcomer warn others and refuse to listen, and when enough of the town agrees, the newcomer is run out and the game ends with a summary of what gave them away.

**Architecture:** A `Suspicion` model holds a score per villager. Direct whispers raise it in code; a Jev Score question inside the existing turn request raises it when a player-rooted rumor reaches a villager; a new addressed-delivery path lets a `warn` message wake its target without a topic match. A periodic engine check turns the influence-weighted town level into a four-level label and, held high for several checks, into a lost game. Goals all met is a won game through the same state machine.

**Tech Stack:** The shipped Rumor Mill (Python 3.12, FastAPI, `mcp`, `typesafe-sdk`, `anthropic`), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-20-rumor-mill-village-life-design.md`, sections "Suspicion and losing" and "Addressed delivery" (slice 0). Read both first. This plan covers slice 0 only; slices 1-5 get their own plans against the code this one ships.

**Branch:** `feat/rumor-mill-suspicion`, cut from the head of the open stack (`feat/rumor-mill-shrug-hints`, PR #475) unless those PRs have merged, in which case cut from `main`. Do not merge or push to anyone else's branch; Rob merges.

## Global Constraints

- Everything lives under `examples/rumor_mill/` (plus `README.md` there and `examples/README.md` if it lists behaviors); **no changes under `src/`**.
- Python 3.12; imports at the top of each module; docstrings short; comments say what code does, no history/meta comments; no inline imports; do not leak internals to clients.
- Every Jev Choice has at most 255 options; Jev decides, Haiku only writes words.
- Villagers still wake only on push or on an addressed message; idle costs no tokens. No per-tick model call.
- A whisper reaches a villager only if it matches one of their needs (rumors still wake by meaning). Only messages addressed to one villager bypass matching.
- Never expose suspicion scores, villagers' needs, or who suspects, to the terminal or the browser: only the four-level label (`quiet`, `murmurs`, `watched`, `hunted`) and in-world lines. Never print or log API keys.
- Each commit passes `ruff check .` and `pytest examples/rumor_mill`; one commit per task, imperative subject, body says why, ends with the attribution line the session specifies.
- Constants are `Config` fields with `RUMOR_MILL_*` env overrides, defaults below (starting values; Task 7 tunes them): `suspicion_whisper_bump=0.08`, `suspicion_hear_max=0.25`, `suspicion_decay_per_day=0.03`, `warn_threshold=0.4`, `refuse_threshold=0.7`, `run_out_level=0.3`, `run_out_checks=3`, `suspicion_check_days=0.25`.

## Review Focus

Failure modes the spec implies but no task's tests would otherwise exercise:

1. A villager over a threshold leaves town (or never existed) between a check and an action: no `KeyError`, no divide by zero when the town is empty (Task 1, Task 5).
2. After the game is lost or won, turns and whispers still in flight must not print further feed lines or change state; a whisper after game over is refused with a message (Task 5).
3. A Jev reply with no suspicion Score (no player-rooted item, or an error) changes nothing and never fails the turn (Task 4).
4. An addressed message delivered to a villager mid-cooldown or whose poll also matched the same utterance appears once in their turn, is not lost, and is not repeated on the next turn (Task 2).
5. The sidebar label and every API response never carry a number or a villager id for suspicion; summary text is LLM/player-controlled so the page renders it as text only (Task 6).

## File Structure

```
examples/rumor_mill/
  suspicion.py   Suspicion: scores, bumps, decay, weighted town level, label, evidence
  game.py        Game: playing/won/lost state, summary, finished event
  (modified) config.py engine.py decide.py actions.py effects.py writer.py player.py
             feed.py web.py cli.py __main__.py static/index.html README.md
  tests/         test_suspicion.py test_game.py, plus additions to the existing test modules
```

---

### Task 1: The suspicion model

**Files:**
- Create: `examples/rumor_mill/suspicion.py`, `examples/rumor_mill/tests/test_suspicion.py`
- Modify: `examples/rumor_mill/config.py` (the constants above), `examples/rumor_mill/tests/test_config.py`

**Interfaces:**
- Produces:
  - `Suspicion(config, town)`.
  - `bump(vid: str, amount: float, reason: str, uid: str | None = None) -> float` (adds, clamps to [0, 1], records `Evidence(vid, amount, reason, uid)`, returns the new score; unknown or departed villagers are ignored and return 0.0).
  - `rate(vid: str, strength: float, reason: str, uid: str | None = None) -> float` (a Jev Score normalized to [0, 1] becomes `bump(vid, strength * config.suspicion_hear_max, ...)`).
  - `score(vid) -> float`; `decay(days: float) -> None` (subtracts `suspicion_decay_per_day * days` from every score, floor 0).
  - `level() -> float` (sum of score × weight over living villagers divided by the sum of weights; `0.0` for an empty town); `weight(villager) -> float` (1.0 default; 3.0 for an occupation containing `mayor`, 2.0 for `priest`, `innkeeper`, `constable`, `reeve`; case-insensitive substring; table is a module constant).
  - `label() -> str` from `level() / config.run_out_level`: below 0.25 `quiet`, below 0.5 `murmurs`, below 0.8 `watched`, otherwise `hunted`.
  - `over(threshold) -> list[str]` ids at or above it; `top_evidence(n=3) -> list[Evidence]` the largest recorded contributions (grouped by `(vid, reason)` and summed).
  - `Evidence` dataclass `(vid, amount, reason, uid)`.

- [ ] **Step 1: Write the failing tests** in `test_suspicion.py`: `test_bump_clamps_to_one_and_records_evidence`, `test_unknown_villager_is_ignored`, `test_decay_floors_at_zero`, `test_level_weights_influential_roles` (a mayor at 1.0 among 3 others at 0 gives `3/6`), `test_level_of_empty_town_is_zero` (Review Focus 1), `test_label_bands` (ratios 0.1, 0.3, 0.6, 1.2 map to the four labels), `test_rate_scales_by_hear_max`, `test_top_evidence_groups_and_orders`, `test_departed_villager_leaves_level` (a villager removed from the town no longer counts). `test_config.py`: each new field has its default and an env override.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_suspicion.py examples/rumor_mill/tests/test_config.py -v` — Expected: FAIL (module and fields missing).
- [ ] **Step 3: Implement** `Suspicion` and the `Config` fields. Scores live in a dict keyed by villager id; every read checks `vid in town`.
- [ ] **Step 4: Run** the same command plus `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Add the suspicion model`.

---

### Task 2: Addressed delivery

**Files:**
- Modify: `examples/rumor_mill/engine.py`, `examples/rumor_mill/effects.py` (the `Hub` protocol), `examples/rumor_mill/tests/test_engine.py`, `examples/rumor_mill/tests/test_effects.py`

**Interfaces:**
- Consumes: `Engine.deliver`, `Engine._turn`, `Mind.poll`, `Match`.
- Produces:
  - `Hub.deliver(utterance, listener_ids, addressed: bool = False)` and `Engine.deliver(..., addressed=False)`.
  - For `addressed=True` the engine still calls `mind.hear(...)` for each listener, and additionally appends a `Match(data_key=f"utt:{utterance.id}", data=utterance.to_data(day))` to `engine._pending[vid]` and sets that villager's wake event. Wake events become `engine._wakes: dict[str, asyncio.Event]` (one per villager, created in `add_villager`, removed in `remove_villager`).
  - `Engine._turn` merges the villager's pending matches into `new` (deduplicated by `data_key` against the poll's matches) and clears them; a turn runs when `new` is non-empty from either source.
  - `on_heard` receives the merged list.

- [ ] **Step 1: Write the failing tests** (FakeMind as in the existing engine tests, patched `decide`/`act`): `test_addressed_message_wakes_a_villager_whose_needs_did_not_match` (the poll returns nothing; the villager still takes a turn and the message is in `view.new`), `test_unaddressed_delivery_does_not_bypass_matching`, `test_addressed_message_also_matched_by_poll_appears_once` and `test_pending_is_cleared_after_the_turn` (Review Focus 4), `test_addressed_message_during_cooldown_is_kept_for_the_next_turn`, `test_addressed_message_to_a_departed_villager_is_dropped_quietly`. Existing effects tests that use a recording hub keep passing with the new keyword.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_effects.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the pending list and the shared wake event in `Engine`; keep `_work`'s coalescing and cooldown unchanged. Remove the pending list entry in `remove_villager`.
- [ ] **Step 4: Run** the same command, the full `pytest examples/rumor_mill`, and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Let a message addressed to one villager wake them`.

---

### Task 3: Whispers raise suspicion and suspicious villagers turn you away

**Files:**
- Modify: `examples/rumor_mill/player.py`, `examples/rumor_mill/feed.py`, `examples/rumor_mill/engine.py` (construct and expose `suspicion`), `examples/rumor_mill/__main__.py` (wire it), `examples/rumor_mill/writer.py`, tests for each

**Interfaces:**
- Consumes: `Suspicion` (Task 1), `Writer.complete`.
- Produces:
  - `Engine.suspicion: Suspicion` (built in `Engine.__init__` from `config` and `town`).
  - `Player.whisper_to(...)`: before delivering, if `suspicion.score(target.id) >= config.refuse_threshold`, the whisper is not delivered; the player gets an in-character refusal via `feed.on_refusal(villager_id, name, line)` (a `refusal` feed event; terminal prints `<Name> turns away from you: "<line>"` or `<Name> turns away from you.` when no line) and the target's suspicion is bumped by `suspicion_whisper_bump` (reason `"tried to whisper again"`). Otherwise the whisper proceeds and, after a successful delivery, `suspicion.bump(target.id, config.suspicion_whisper_bump, "whispered to them directly", uid)` (a landed whisper and a shrug both count).
  - `Writer.turn_away(villager) -> str`: one short in-character line refusing to hear the newcomer (about 15 words, villager's voice, no stage directions or quotes, no mention of suspicion scores); a failure falls back to no line.
  - `Feed.on_refusal(villager_id, name, line)` emits kind `refusal` with `name` and `line`.

- [ ] **Step 1: Write the failing tests**: `test_whisper_raises_the_recipients_suspicion_even_when_it_shrugs`, `test_whisper_to_a_suspicious_villager_is_refused_and_not_delivered` (the stub engine records no delivery), `test_refusal_bumps_suspicion_and_prints_a_line`, `test_refusal_falls_back_without_a_line_when_haiku_fails`, `test_refusal_event_carries_no_score` (the event dict has no numeric suspicion), `test_failed_delivery_does_not_bump` (a delivery error leaves the score unchanged). Writer: the `turn_away` prompt carries the villager profile and asks for one short refusal.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_feed.py examples/rumor_mill/tests/test_writer.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the player, writer, and feed changes and wire `Suspicion` in `__main__.py`/`Engine`.
- [ ] **Step 4: Run** the same command, the full suite, and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Make whispering raise suspicion and let suspects turn you away`.

---

### Task 4: Jev rates suspicion, and suspects warn

**Files:**
- Modify: `examples/rumor_mill/decide.py`, `examples/rumor_mill/actions.py`, `examples/rumor_mill/effects.py`, `examples/rumor_mill/engine.py`, `examples/rumor_mill/writer.py`, `examples/rumor_mill/feed.py`, tests for each

**Interfaces:**
- Consumes: Task 1, Task 2 (`addressed=True`), the shipped `Decision`, `TurnView`.
- Produces:
  - `TurnView` gains `suspicion: float` and `player_rooted: list[Match]` (the new items whose utterance is player-rooted, from `lineage.is_player_rooted(m.data["id"])`).
  - `actions.WARN = "warn"` with description "Warn one specific person nearby that the newcomer is behind the rumors." It is in `ACTIONS`, `TARGET_KIND` (`villager`), `STYLED`, and `NEEDS_SOMEONE`; `available_actions` offers it only when `view.suspicion >= warn_threshold` (the threshold is passed in `TurnView.warn_threshold`).
  - `decide.py`: the Jev state gains `suspicion_of_the_newcomer` as words (`"none"`, `"a little"`, `"quite a bit"`, `"convinced"`), never a number. When `view.player_rooted` is non-empty the request gains a `suspicion` Score question (levels: `not at all`, `a little`, `quite a bit`, `convinced`; instructions: how strongly does what they just heard make this villager suspect the newcomer is behind these rumors, given their personality). `Decision` gains `suspicion_strength: float | None` in [0, 1] (the Score answer normalized), `None` when the question was not asked or the answer is missing.
  - `Engine._turn` applies `suspicion.rate(villager.id, strength, "heard the newcomer named", uid)` when `decision.suspicion_strength` is not None, before `act`; failures in rating never fail the turn.
  - `effects.act` handles `WARN`: Haiku writes one line (`Writer.warn(villager, target)`) in the villager's voice warning that the newcomer is behind the rumors; delivered with `addressed=True` to the target only; utterance verb `warned`, parent None. The target's suspicion is bumped through `engine.suspicion` via the hub (`Hub.suspect(vid, amount, reason, uid)`; the recording test hub stubs it) by `0.5 * suspicion_hear_max` with reason `"was warned about the newcomer"`.
  - `Feed.on_warning(speaker_id, speaker_name, target_id, target_name)` emits kind `warning`; the terminal prints `<Speaker> warned <Target> about you.` Players see the warning but not the words.

- [ ] **Step 1: Write the failing tests**: decide: `test_suspicion_question_only_when_a_player_rooted_item_is_new` and `test_missing_suspicion_answer_leaves_strength_none` (Review Focus 3), `test_warn_offered_only_above_threshold_and_with_someone_present`, `test_state_shows_suspicion_in_words_not_numbers`, `test_warn_maps_target_and_style`; effects: `test_warn_is_addressed_to_the_target_only`, `test_warn_raises_the_targets_suspicion`; engine: `test_suspicion_strength_updates_scores_and_a_rating_error_does_not_fail_the_turn`; feed: `test_warning_event_has_names_and_no_line_or_score`; writer: `warn` prompt asks for one line and never says how suspicious anyone is.
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests -k "decide or effects or engine or feed or writer" -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Check the installed `typesafe_sdk` for the `Score` answer attribute and its range before normalizing (`response.scores[id].score`); do not guess.
- [ ] **Step 4: Run** the same selection, the full suite, and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Let Jev rate suspicion and let suspects warn others`.

---

### Task 5: The town check and the game state

**Files:**
- Create: `examples/rumor_mill/game.py`, `examples/rumor_mill/tests/test_game.py`
- Modify: `examples/rumor_mill/engine.py`, `examples/rumor_mill/judge.py`, `examples/rumor_mill/feed.py`, `examples/rumor_mill/player.py`, `examples/rumor_mill/cli.py`, `examples/rumor_mill/__main__.py`, tests for each

**Interfaces:**
- Consumes: `Suspicion`, `Judge.run`, `Feed`.
- Produces:
  - `Game(feed)`: `state` (`"playing"`, `"won"`, `"lost"`), `finished: asyncio.Event`, `lose(summary: Summary)`, `win(summary: Summary)` (the first call wins; later calls are ignored), `Summary` dataclass `(outcome, goals: list[tuple[str, int]], reasons: list[str])`.
  - `Engine._suspicion_loop()`: every `suspicion_check_days * day_seconds` it decays suspicion by that many days, computes `level()` and `label()`, emits a feed `heat` event when the label changes, and counts consecutive checks with `level() >= run_out_level`. After `run_out_checks` in a row (a dip resets the count) it calls `game.lose(...)` with `reasons` from `suspicion.top_evidence()` rendered as plain sentences (`"Marta Goodwife heard it from you directly."`, `"Piers Tanner was warned about the newcomer."`), a `town meeting` announcement line through the feed, and goal percentages from the judge.
  - `Judge.run` calls `game.win(...)` when all goals are met; the judge and the suspicion loop both stop once `game.finished` is set.
  - After the game finishes: `Player.whisper_to` returns `"The game is over."` without delivering; engine turns in flight finish silently (no feed events after the finish: gate `Feed.emit` on a `closed` flag set by `Game`); `Feed` gets `on_game_over(summary)` emitting kind `gameover` with the summary lines.
  - Terminal: the `goals` command also prints the heat label; on game over the REPL prints the summary and exits.
  - `__main__` creates `Game`, passes it to engine/judge/player/web, and ends the session when `game.finished` is set (terminal exits; web keeps serving the summary).

- [ ] **Step 1: Write the failing tests** (injected tiny `day_seconds`, `run_out_checks=2`, a stub town with villagers at known scores): `test_run_out_after_consecutive_checks_at_the_level`, `test_a_dip_resets_the_count`, `test_label_event_only_when_it_changes`, `test_loss_summary_names_what_gave_you_away_and_includes_goal_progress`, `test_empty_town_does_not_lose_by_division` (Review Focus 1), `test_nothing_prints_after_the_game_is_over`, `test_whisper_after_game_over_is_refused` (Review Focus 2), `test_winning_through_the_judge_ends_the_game_once`, `test_first_finish_wins_the_race` (a loss and a win at once keep only the first), `test_stop_leaves_no_pending_tasks` (the new loop is cancelled with the others).
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_game.py examples/rumor_mill/tests/test_engine.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `Game`, the engine loop, the judge hook, the player gate, and the wiring.
- [ ] **Step 4: Run** the same command, the full suite, and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `End the game when the village runs the newcomer out`.

---

### Task 6: The heat label and the game-over screen in the browser

**Files:**
- Modify: `examples/rumor_mill/web.py`, `examples/rumor_mill/static/index.html`, `examples/rumor_mill/README.md`, `examples/rumor_mill/tests/test_web.py`

**Interfaces:**
- Consumes: `Feed` events `heat`, `warning`, `refusal`, `gameover`; `Game`; `Suspicion.label()`.
- Produces:
  - `/api/state` gains `heat` (the label string) and `game` (`{state, summary|null}` where the summary has only `outcome`, `goals` and `reasons` lines).
  - The WebSocket carries the new event kinds. A `warning` line renders as `<Speaker> warned <Target> about you.`; a `refusal` as the villager's name and line; `heat` updates the sidebar label.
  - The sidebar gains one quiet line under Goals showing the label in plain words (for example `Folk are watching you.` for `watched`; copy for all four, sentence case, no numbers, no bars); keyboard-focus-neutral and `aria-live=polite`.
  - On `gameover` the page shows a full-width panel over the feed (not a modal that traps focus) with the outcome, the goal percentages, and the reasons list, all via `textContent`; the whisper input is disabled with the text `The game is over.`; a line says to restart the program for a new game.
  - README: a section on suspicion: how it is raised, what a warning and a refusal mean, the four labels, and losing; plain style, no marketing words, no time estimates.

- [ ] **Step 1: Write the failing tests**: `test_state_carries_the_label_and_no_scores` and `test_gameover_event_reaches_the_websocket` (Review Focus 5), `test_state_after_loss_carries_the_summary`, `test_no_api_response_contains_a_suspicion_number_or_villager_id_for_suspects` (walk every JSON response and event of a scripted game for numeric suspicion fields or the suspect ids).
- [ ] **Step 2: Run** `pytest examples/rumor_mill/tests/test_web.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the server and the page changes. Verify the page in a real browser at desktop and phone width, light and dark: the label changes, a warning and a refusal render, the game-over panel appears and the input is disabled. Screenshots go to the scratchpad.
- [ ] **Step 4: Run** the same test, the full suite, and `ruff check .` — Expected: PASS.
- [ ] **Step 5: Commit** `Show the village's mood and the end of the game in the browser`.

---

### Task 7: Play it and tune it

**Files:**
- Modify: whichever module owns a tuned value; `examples/rumor_mill/README.md` for final defaults

**Interfaces:** none.

- [ ] **Step 1: Play real sessions** (real keys, a local Contex, 25-100 villagers) and record: a quiet player who whispers rarely survives the session; a careless player whispering to many villagers in a row gets warnings, refusals, the label rising, and the loss within a few game days; laying low lets the label fall; a loss summary names real reasons. Tune `suspicion_whisper_bump`, `suspicion_hear_max`, `suspicion_decay_per_day`, the thresholds, `run_out_level`, and the influence weights until losing is possible but not the default outcome of a normal session, and report the numbers and example summaries.
- [ ] **Step 2: Check cost:** the usage line for a session with the suspicion Score question versus the earlier baseline (the extra Jev tokens should be small).
- [ ] **Step 3: Run** `pytest examples/rumor_mill -v && ruff check .` — Expected: PASS.
- [ ] **Step 4: Commit** each tuning change with the module it belongs to, then `Document the suspicion defaults` for the README.
- [ ] **Step 5: Push the branch and open the PR** against the right base (the open stack's head, or `main` if merged), linking the spec and this plan. Do not merge.

---

## Self-Review

- **Spec coverage:** per-villager score from whispers and Jev (Tasks 1, 3, 4); decay and no cheat code (1); spread by `warn`, refusal (3, 4); addressed delivery for warnings (2); town level with influence weights, consecutive-check loss, town meeting line, summary of what gave you away (1, 5); win and lose through one state machine (5); coarse label plus in-feed behavior as warnings (3, 4, 6); one extra Score question inside an existing request, no extra call (4); label never a number and never who (1, 6). Child blabbing and kin interactions are explicitly later slices.
- **Interfaces:** `Hub.deliver(..., addressed)`, `Hub.suspect`, `Suspicion.bump/rate/level/label/top_evidence`, `Game.lose/win`, `Feed.on_refusal/on_warning/on_game_over` are named the same wherever they recur.
- **Proportion:** the plan records interfaces, rules and test names; bodies are left to the implementer.
