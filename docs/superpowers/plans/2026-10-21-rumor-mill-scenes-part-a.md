# Rumor Mill: Scenes, Openings, and a Moving Village (Part A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Heated exchanges keep going and pull in neighbours, a brush-off or near miss leaves the player an opening, and villagers walk a daily round so rumors travel between places.

**Architecture:** A new `scenes.py` holds scene state and the momentum rules; the engine starts a scene when a villager answers a line spoken at their place, addresses later scene lines to the participants (and to bystanders it draws in), and ends the scene on the spec's four conditions. The keen-to-keep-talking Score rides on the existing turn request. `Player` keeps a brush-off thread per villager and reports near misses. A new `routine.py` gives each villager a work and leisure place, cached next to the seed, and the engine's clock loop walks villagers there in batches using the existing move-and-carry path.

**Tech Stack:** The Rumor Mill as on `feat/rumor-mill-scenes` (Python 3.12, FastAPI, `typesafe-sdk`, `anthropic`, pytest + pytest-asyncio, one static page).

**Spec:** `docs/superpowers/specs/2026-10-21-rumor-mill-scenes-design.md`, Part A only (A1, A2, A3). Part B gets its own plan.

**Branch:** `feat/rumor-mill-scenes` (the spec commit is on it). Rob merges; do not merge.

## Global Constraints

- Everything under `examples/rumor_mill/`; **no changes under `src/`**.
- Python 3.12; imports at the top, never inline; concise docstrings; comments say what code does, never its history; no clever code. Follow the owner's banned-word list in `~/.claude/CLAUDE.md`.
- No momentum, keen score, clearance, baseline, margin, need, or suspicion number reaches the terminal, the browser, or a log line above DEBUG.
- The published utterance payload stays `id` and `text` only.
- Page text from villagers, the player, or a model goes in through `textContent` or text nodes only. New controls are `<button>`s on the page's roving focus (`ROVE`), toggles carry `aria-expanded`, and nothing new animates outside `.fresh` (which `prefers-reduced-motion` already stills).
- New Config fields with `RUMOR_MILL_*` overrides: `max_scenes=3`, `scene_cooldown_seconds=5.0`, `join_rate=0.3`, `scene_max_lines=12`, `hint_margin=0.0`, `near_miss_band=0.01`.
- Cost bounds: one Jev request and one Haiku line per scene line, capped by `scene_max_lines`; the keen question rides on the existing turn request; a routine move costs at most the carried share's Haiku line; place choices cost one Jev request per villager, once, cached in `<seed>.places.json`.
- Each commit passes `python -m pytest examples/rumor_mill -q` and `ruff check examples/rumor_mill`; one commit per task; imperative subject; body says why; ends with the session's attribution line.

## Review Focus

1. A scene whose participants stop answering (a failed turn, a departure) must free its slot, not hold one of the `max_scenes` forever (Task 2: `test_a_failed_turn_leaves_the_scene`, `test_a_participant_leaving_town_ends_the_scene`).
2. A villager who answers the newcomer mid-scene leaves the scene; the newcomer's own whisper never opens or joins one (Task 2: `test_answering_the_newcomer_leaves_the_scene`, `test_the_newcomers_whisper_never_opens_a_scene`).
3. A brush-off thread across a change of the day's part, or after its last try, starts a fresh card; a reply never threads onto a stale one (Task 5: `test_a_thread_closes_at_the_next_part_of_the_day`, `test_a_thread_takes_two_whispers_then_starts_a_new_card`).
4. A regenerated seed that reuses villager ids must not reuse their old places (Task 7: `test_a_renamed_or_invalid_entry_is_asked_again`).
5. A routine move for a villager who left town between batches must not put them back on the map (`Town.move` writes `_places` for any id) (Task 8: `test_a_departed_villager_is_not_moved`).

---

### Task 1: Scene rules and the keen question

**Files:**
- Create: `examples/rumor_mill/scenes.py`, `examples/rumor_mill/tests/test_scenes.py`
- Modify: `decide.py`, `rumors.py`, `config.py`; `tests/test_decide.py`, `tests/test_rumors.py`, `tests/test_config.py`; move `Always` from `tests/test_overhear.py` to `tests/helpers.py`

**Interfaces:**
- Produces in `decide.py`: `KEEN_LEVELS = ["not at all", "a little", "quite a bit", "can't stop"]`; `KEEN_INSTRUCTIONS = "How much does this villager want to keep talking about this?"`; `TurnView.ask_keen: bool = False`; `Decision.keen: float | None = None` (last field). When `ask_keen`, `_questions` adds `"keen": Score(...)`, and `decide` fills `keen` with `_scaled(resp, "keen", KEEN_LEVELS)`.
- Produces in `rumors.py`: `Card.topic_only: bool = False`; `async Rumors.add_topic(uid: str, text: str, published_at: datetime) -> None` (a card that is measured and published like `add` so `on_topic(uid, ...)` works, left out of `cards()`); `Rumors.carries(root_uid: str, uid: str) -> bool` (uid is an on-topic hop shown on root's card, following `part_of`).
- Produces in `scenes.py`:
  - `SCENE_SPEECH = (RETELL, DENY, CONFRONT, WARN, SPEAK)`; `MAX_PEOPLE = 6`; `FLOOR = 0.25`; `DECAY = 0.4`; `LEAVE_DROP = 0.2`; `JOIN_BONUS = 0.1`; `OPENING_HEAT = {"heated": 0.6, "lively": 0.45, "plain": 0.3}`; `STYLE_BONUS = {"heated": 0.15, "lively": 0.05, "plain": 0.0}`.
  - `heat(action: str | None, style: str | None) -> str`: `"heated"` for `overhear.HEATED_ACTIONS` or `HEATED_STYLES`, `"lively"` for `LIVELY_STYLES`, else `"plain"`.
  - `@dataclass Scene`: `id: str`, `place: str`, `topic: str` (card uid the on-topic check uses), `rumor: str | None` (the whisper for a player-rooted scene), `heated: bool`, `opening: str`, `participants: set[str]`, `people: set[str]` (everyone who spoke in it), `lines: list[str]`, `momentum: float`, `ended: str | None = None` (`"veered"`, `"cooled"`, `"long"`, `"alone"`).
  - `Scenes(config: Config, town: Town, rng: random.Random)` with: `of(vid) -> Scene | None`; `holding(uid) -> Scene | None`; `start(opening: Utterance, answerer: str, topic: str, rumor: str | None) -> Scene | None` (None at `max_scenes`; momentum = `OPENING_HEAT[heat(opening)]`; lines = `[opening.id]`; ids `s-1`, `s-2`, ...); `join(scene, vid) -> bool` (False at `MAX_PEOPLE`); `speaking(u: Utterance) -> Scene | None` (the speaker's scene when `u.place == scene.place` and `u.action in SCENE_SPEECH`); `said(scene, uid) -> None`; `addressed(scene, speaker: str) -> list[str]` (other participants at the place, then each bystander at the place, never `PLAYER_ID`, drawn with `rng.random() < momentum * join_rate` while under `MAX_PEOPLE`); `turned(scene, vid, action: str | None, style: str | None, keen: float | None, joined: bool) -> str | None`; `end(scene, reason) -> None`.
  - `turned`: speech adds `(keen or 0) + STYLE_BONUS[heat] + (JOIN_BONUS if joined) - DECAY` and adds vid to `people`; anything else (including `None`, used for a failed turn or a departure) removes vid from `participants` and subtracts `DECAY + LEAVE_DROP`; momentum clamps to [0, 1]. Then it returns the first end that holds: `"alone"` (fewer than two participants at the place), `"cooled"` (momentum < `FLOOR`), `"long"` (`len(lines) >= scene_max_lines`), else None.

- [ ] **Step 1: Write the failing tests.** `test_decide.py`: `test_keen_is_asked_only_when_requested_and_scaled` (`view(ask_keen=True)`, `StubJev(..., scores={"keen": 2})` gives `keen == pytest.approx(2/3)`; without `ask_keen`, no `"keen"` question and `keen is None`). `test_rumors.py`: `test_a_topic_is_checked_but_never_shown` (`add_topic("u-5", ...)`, then `on_topic("u-5", ...)` measures against it and `cards()` has only `w-1`); `test_carries_names_only_on_topic_hops`. `test_scenes.py`: `test_opening_heat_sets_the_starting_momentum` (confront 0.6, `delighted` 0.45, `faithful` speak 0.3); `test_no_more_than_max_scenes_run`; `test_a_keen_heated_line_raises_momentum_and_a_dull_one_cools_it` (plain opening 0.3; keen 1.0 in `mock` → 1.0, clamped; keen 0.0 `faithful` → 0.6; keen 0.0 again → 0.2 and `"cooled"`); `test_a_non_speech_turn_leaves_and_two_becomes_alone`; `test_the_line_cap_ends_it` (`scene_max_lines=3`); `test_join_stops_at_six`; `test_addressed_draws_bystanders_at_momentum_times_join_rate` (`Always(0.0)` draws all bystanders, `Always(0.99)` none, never the speaker or the player); `test_speaking_needs_speech_at_the_scene_place`. `test_config.py`: the six new defaults.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_scenes.py examples/rumor_mill/tests/test_decide.py examples/rumor_mill/tests/test_rumors.py examples/rumor_mill/tests/test_config.py -q`. Expected: FAIL (no `scenes` module, no `keen`).
- [ ] **Step 3: Implement** the interfaces above. `Scenes` keeps active scenes in a dict by id; ended scenes are dropped.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Add scene rules and ask how keen a villager is to keep talking`.

---

### Task 2: Scenes start, keep going, and end

**Files:** Modify `engine.py`, `feed.py`; `tests/test_engine.py`, `tests/test_feed.py`, `tests/test_web.py`.

**Interfaces:**
- Consumes: Task 1 `Scenes`, `Scene`, `SCENE_SPEECH`, `TurnView.ask_keen`, `Decision.keen`, `Rumors.add_topic`, `Rumors.carries`.
- Produces in `feed.py`: `SCENE_ENDS = {"veered": "The {kind} turned to other things.", "cooled": "The {kind} died down.", "long": "The {kind} ran its course.", "alone": "The {kind} broke up."}`; `Feed.on_scene(scene: Scene) -> None` emits kind `"scene"` with `scene_id`, `rumor`, `opening`, `place`, `kind` (`"argument"` when heated, else `"conversation"`), `people` (`len(scene.people)`), `lines` (`len(scene.lines)`), `ended` (the filled sentence or None); no terminal line, no momentum. `on_delivery(..., scene: str | None = None)` puts `scene` on hop events.
- Produces in `engine.py`: `self.scenes = Scenes(config, town, random.Random())`; `_exchange(m: Match, vid: str, place: str) -> Utterance | None` (the fact for a line spoken at this place by someone else who is still here, never the newcomer); `async _enter_scene(v: Villager, decision: Decision, place: str) -> tuple[Scene | None, bool]` (returns the villager's current scene; else, for a speech decision whose source is an `_exchange`, starts one: player-rooted when `rumors.carries(root, opening.id)`, with `topic = rumor = root`; village talk when `place == self.player_place()`, with `topic = opening.id`, `rumor = None`, after `await rumors.add_topic(...)`; never without `rumors`; emits `on_scene` on start); `_scene_turn(scene, vid, decision, joined) -> None` (calls `turned`, then ends or emits `on_scene`); `_end_scene(scene, reason) -> None` (`scenes.end`, then `on_scene`).
- `_turn`: `ask_keen = scenes.of(v.id) is not None or any(self._exchange(m, v.id, place) for m in new)`; `_enter_scene` runs after `decide` and before `act`; `_scene_turn` after `act`.
- `deliver`: when `scenes.speaking(utterance)` gives a scene, the `addressed(scene, speaker)` ids join the listeners before publishing; after the on-topic check (the existing `on_topic` for a player-rooted scene, else `await rumors.on_topic(scene.topic, utterance.text)`), an on-topic line is `said` and `hear_directly`-ed to those ids, and an off-topic one calls `_end_scene(scene, "veered")` with nobody addressed.
- `cooldown_ends(vid)` uses `scene_cooldown_seconds` while `scenes.of(vid)`. `_leave_scene(vid) -> None` calls `turned(scene, vid, None, None, None, False)` and ends the scene on any reason; `remove_villager` and the failed-turn branch of `_work` call it.

- [ ] **Step 1: Write the failing tests** in `test_engine.py`, with a `talkers` fixture (each turn takes the next planned `(action, keen)`; a speech turn records a fact for a line `"more"` at the speaker's place, parented on the source, and delivers it to the source's speaker) and a helper `said_by(engine, uid, speaker, place="square", action=SPEAK, style=None)` that records a fact. Tests: `test_an_answered_line_at_the_players_place_starts_a_scene` (scene with participants `{v1, v2}`, a `scene` event with `people == 2`, v1's next view holds v2's line); `test_village_talk_away_from_the_player_starts_no_scene`; `test_an_on_card_hop_starts_a_scene_anywhere_and_an_off_card_one_does_not` (`stub_rumors(0.9)`); `test_the_newcomers_whisper_never_opens_a_scene`; `test_scene_lines_are_addressed_and_wait_only_the_scene_cooldown` (`Config(cooldown_seconds=5, scene_cooldown_seconds=0.05)`: v1 answers within `TIMEOUT`); `test_max_scenes_holds_back_a_new_scene` (`max_scenes=1`); `test_an_off_topic_line_ends_the_scene_and_is_not_addressed` (event `ended == "The conversation turned to other things."`, `engine._pending` has no entry for the listener); `test_a_non_speech_turn_breaks_up_a_two_person_scene`; `test_a_failed_turn_leaves_the_scene`; `test_a_participant_leaving_town_ends_the_scene`; `test_answering_the_newcomer_leaves_the_scene`; `test_keen_is_asked_only_when_a_scene_could_follow`. `test_feed.py`: `test_scene_event_carries_counts_and_no_momentum` (keys exactly `scene_id, rumor, opening, place, kind, people, lines, ended`); update hop-event assertions for `scene`. `test_web.py`: `test_scene_event_reaches_the_websocket` (the existing `walk` check still finds no suspicion number).
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_engine.py examples/rumor_mill/tests/test_feed.py examples/rumor_mill/tests/test_web.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Let an exchange become a scene that keeps going until it cools`.

---

### Task 3: Scenes draw others in, and the player hears all of one

**Files:** Modify `engine.py`, `feed.py`; `tests/test_engine.py`.

**Interfaces:**
- Consumes: Task 1 `Scenes.holding`, `Scenes.join`, `Scenes.addressed` (bystander draws); Task 2 `_enter_scene`, `deliver` scene path.
- `_enter_scene`: a speech decision whose source line is held by a scene joins it when `scenes.join` allows and returns `(scene, True)`; at `MAX_PEOPLE` the line is ordinary talk.
- `Feed.on_overheard(utterance, reason, scene: str | None = None)` carries `scene`. In `deliver`, an on-topic line of a village-talk scene at `self.player_place()` goes to `on_overheard(u, "scene", scene.id)` and skips `Overhear.keep`; any other line keeps today's rules.

- [ ] **Step 1: Write the failing tests:** `test_a_drawn_bystander_who_answers_joins_the_scene` (`engine.scenes.rng = Always(0.0)`, v3 at the place gets the line addressed and joins; `people == 3`); `test_a_bystander_hearing_through_their_needs_joins_the_same_way` (v3 polls the scene line, no draw); `test_a_full_scene_turns_a_seventh_speaker_away`; `test_every_line_of_a_village_scene_is_overheard` (`overhear_rate=0.0`; reasons are all `"scene"` with the scene id); `test_a_player_rooted_scene_line_is_a_hop_not_overheard`. Update `test_overheard_lines_reach_the_feed_but_card_hops_do_not` for `"scene": None`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_engine.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Draw bystanders into a lively scene and let the player hear it all`.

---

### Task 4: Show scenes on the page

**Files:** Modify `static/index.html`.

**Interfaces:**
- Consumes: Task 2 `scene` events and `scene` on hops; Task 3 `scene` on overheard lines.

- A `scene` event with a `rumor` goes to that card: a new `ol.scenes` sits between the card's tally and its retellings toggle; each scene is an `li.scene[data-scene]` with a head (`An argument at the tavern: 4 people, 9 lines`, or `A conversation at ...`), a `button.linkish.scene-more` toggle (`aria-expanded`, `tabIndex: -1`, text `Show lines` / `Hide lines`, added to `ROVE`), an `ol.scene-lines` hidden while the scene is not `.open` (as `.rumor:not(.open) .thread`), and a `p.scene-end` with `ended` once set. On first sight it moves the opening hop (`.thread .hop[data-uid=opening]`) into its lines and recounts the thread. A hop with `scene` goes into that scene's lines instead of `.thread`.
- A `scene` event without a `rumor` makes or updates a `.vt[data-scene]` block from `talkBlock(place)`; `overheard` sends lines with `scene` to that block; `countTalk` heads a scene block with the scene's words (`An argument at the tavern · 6 lines`) and shows its `ended` line in a `p.scene-end`.
- All text by `textContent`; no new keyframes.

- [ ] **Step 1: Implement** the page changes.
- [ ] **Step 2: Verify in a browser** against a stub server replaying a scripted feed (a card scene that grows and ends with each `ended` sentence; a village scene at the player's place; a reload mid-scene): arrow keys reach each `scene-more` and Enter toggles it; `aria-expanded` follows; desktop and phone width; light and dark; reduced motion on. Expected: every case renders as described, no console errors.
- [ ] **Step 3: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 4: Commit** `Show scenes on rumor cards and in village talk`.

---

### Task 5: A brush-off is an opening

**Files:** Modify `player.py`, `writer.py`; `tests/test_player.py`, `tests/test_writer.py`.

**Interfaces:**
- Produces in `player.py`: `THREAD_WHISPERS = 2`; `self._threads: dict[str, dict]` with `id` (first whisper uid), `need` (the concern last hinted), `whispers` (count), `at` (`(game.day, game.phase)`, or None without a game); `_thread(vid) -> dict | None` (drops and returns None once the day's part has changed). `check(target, utterance, since, hint_need: str | None = None)`: the hint need's bar is `threshold(need) - match_margin + hint_margin`, every other need's is `threshold(need)`; it queries at the bar and sets clearance against it.
- `whisper_to`: an open engine conversation still decides `utterance.conversation`; otherwise an open thread does, and its `need` is the `hint_need`. A hit drops the thread. A miss opens a thread (`whispers=1`) or counts one more, dropping it at `THREAD_WHISPERS`; the concern is chosen by `_next_concern` before the hint task starts, stored as the thread's `need`, and passed to `_hint(villager_id, name, uid, concern)` and `_brush_off(villager_id, concern)`.
- `Writer.brush_off`: replace the allusion sentences with exactly: `"You may name the subject itself, such as strange lights or the vines, but never a name, a number, or any particular. Never repeat its wording."`

- [ ] **Step 1: Write the failing tests:** `test_a_whisper_after_a_brush_off_is_a_reply_on_the_same_card` (second delivered utterance has `conversation == "w-1"`); `test_a_reply_lands_on_the_hinted_need_at_its_baseline` (`StubBaselines(0.5)`, default `match_margin`, hinted need scores 0.49: one entry in `engine.direct`); `test_the_same_score_misses_a_need_that_was_not_hinted` (`engine.direct == []`); `test_a_thread_takes_two_whispers_then_starts_a_new_card` (conversations `None, "w-1", None`); `test_a_thread_closes_at_the_next_part_of_the_day` (`player.game = Game(player.feed)`, miss, `game.tick()`, next whisper has `conversation is None`); `test_a_reply_raises_suspicion_like_any_whisper` (two whispers: `2 * suspicion_whisper_bump`); `test_an_open_conversation_takes_precedence_over_a_thread`. `test_writer.py`: `test_brush_off_may_name_the_subject_but_no_particulars` (system prompt contains the new sentence and not `"don't name the specific thing"`).
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_writer.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Let the player keep talking after a brush-off`.

---

### Task 6: Near misses lean in

**Files:** Modify `player.py`, `writer.py`, `feed.py`, `static/index.html`; `tests/test_player.py`, `tests/test_writer.py`, `tests/test_feed.py`.

**Interfaces:**
- Consumes: Task 5 threads, `check(..., hint_need)`.
- `check(...) -> list[tuple[str, Match]] | None`: each need is queried at its bar minus `near_miss_band`; each `(need, match)` carries clearance against its bar, so near misses come back with clearance in `[-near_miss_band, 0)`. `whisper_to` takes hits as clearance `>= 0`.
- No hit, best clearance `>= -near_miss_band`, and the thread has a whisper left: status `"near_miss"`; the thread opens or counts as for a miss, with `need` = the near-missed need; `_hint(..., state="near_miss")` adds `hint=await writer.go_on(villager, text)`. On the thread's last whisper, a near miss is a brush-off.
- `Writer.go_on(villager: Villager, newcomer_line: str) -> str`: one in-character line under 15 words urging the newcomer on, never saying what interests the villager; `GO_ON_MAX_TOKENS = 40`.
- `Feed.on_whisper_status`: `near_miss` behaves like `brushed_off` (dropped once answered; terminal prints only once its hint is settled: `'{name} leans in: "{hint}"'` or `"{name} leans in."`). Notes ignore it.
- Page: `chip()` shows `Leaned in: “…”` or `Leaned in.`, no badge.

- [ ] **Step 1: Write the failing tests:** `test_a_near_miss_leans_in_and_opens_a_thread` (`StubBaselines(0.5)`, score 0.495: states `["checking", "near_miss"]`, then the hint; `engine.direct == []`; next whisper has `conversation == "w-1"`); `test_a_near_miss_on_the_last_try_is_a_brush_off`; update `test_check_queries_each_need_at_its_threshold` to `{"bread": pytest.approx(0.49), "flour": pytest.approx(0.89)}`. `test_writer.py`: `test_go_on_urges_the_newcomer_on_in_one_line`. `test_feed.py`: `test_a_near_miss_prints_once_its_line_is_settled`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_player.py examples/rumor_mill/tests/test_writer.py examples/rumor_mill/tests/test_feed.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and ruff, then check the chip in the browser stub from Task 4. Expected: PASS.
- [ ] **Step 5: Commit** `Answer a near miss with a nudge instead of a brush-off`.

---

### Task 7: Each villager's work and leisure place

**Files:**
- Create: `examples/rumor_mill/routine.py`, `examples/rumor_mill/tests/test_routine.py`
- Modify: `population.py`, `__main__.py`, `engine.py` (constructor only); `tests/test_population.py`; move `StubJev` from `tests/test_decide.py` to `tests/helpers.py`

**Interfaces:**
- Produces in `routine.py`: `WORK_INSTRUCTIONS = "Where does this villager spend their working day, given their occupation?"`; `LEISURE_INSTRUCTIONS = "Where does this villager go to unwind in the evening, given their personality?"`; `Routine(jev, path: str)` with `async load(villagers: list[Villager]) -> None`, `async add(v: Villager) -> None`, `place(vid: str, phase: str) -> str | None` (morning and night: work; afternoon: `"square"`; evening: leisure; None for a villager without a routine).
- One Jev request per villager: state `{name, occupation, personality}`, questions `"work"` and `"leisure"`, each a `Choice` over `PLACES` without `GATE`. The sidecar is `{vid: {"name", "work", "leisure"}}` at `path`; an entry whose name differs or whose place is unknown is asked again; an unreadable file counts as empty and is rewritten. A failed request is logged (ids only) and leaves that villager without a routine, so the next run asks again.
- `Engine(..., routine: Routine | None = None)` stores `self.routine`. `Replenisher._admit` awaits `engine.routine.add(newcomer)` before `add_villager` when there is a routine. `__main__` builds `Routine(jev, config.seed_path + ".places.json")`, loads it before adding villagers, starts each villager at `routine.place(v.id, "morning")` (falling back to `rng.choice(places)`), and passes it to `Engine`.

- [ ] **Step 1: Write the failing tests:** `test_load_asks_once_per_villager_and_writes_the_sidecar` (`tmp_path`); `test_load_reuses_the_sidecar` (a second `Routine` asks nothing); `test_a_renamed_or_invalid_entry_is_asked_again`; `test_an_unreadable_sidecar_is_rewritten`; `test_place_follows_the_part_of_the_day`; `test_a_failed_choice_leaves_the_villager_without_a_routine`; `test_add_writes_the_newcomer`. `test_population.py`: `test_a_newcomer_gets_a_routine_on_arrival`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_routine.py examples/rumor_mill/tests/test_population.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Give each villager a work and a leisure place`.

---

### Task 8: The village walks its daily round

**Files:** Modify `engine.py`; `tests/test_game.py` (its `start` helper builds an engine with a game).

**Interfaces:**
- Consumes: Task 7 `Routine.place`; Task 2 `Scenes.of`; existing `act`, `share_chance`.
- Produces in `engine.py`: `ROUTINE_BATCH = 5`; `ROUTINE_SPREAD = 0.5`; `self._latest: dict[str, Match]` (the last new item of each villager's last turn); `self._chose_move: set[str]` (villagers whose own turn moved them this part of the day); `async _walk(phase: str, spread: float) -> None`; `async _stroll(vid: str, place: str) -> None`.
- `_clock_loop`: ticks stay `day_seconds / len(PHASES)` apart; after `tick` and `on_time` it clears `_chose_move` and, with a routine, runs `_walk(phase, phase_seconds * ROUTINE_SPREAD)` inside that time.
- `_walk`: villagers whose routine place differs from where they stand, in batches of `ROUTINE_BATCH`, batches `spread / number_of_batches` apart; each is re-checked when its batch comes (still in town, not in `_turning`, not in a scene, not in `_chose_move`).
- `_stroll`: `act(Decision(MOVE, latest, place, None), ..., share_chance=self.share_chance(vid, latest) if latest else 0.0)`; failures are logged with ids only. `_turn` sets `_latest` and adds to `_chose_move` on `MOVE`; `remove_villager` clears both.

- [ ] **Step 1: Write the failing tests** (a `StubRoutine` mapping vid to one place for every phase): `test_the_clock_walks_villagers_to_their_routine_place`; `test_a_villager_in_a_turn_a_scene_or_a_chosen_move_stays_put`; `test_a_departed_villager_is_not_moved` (`town.remove("v1")` between batches; `"v1"` absent from every `town.present(...)`, no error); `test_a_routine_move_may_carry_the_latest_news` (monkeypatched `engine_mod.act` records `decision.action == MOVE`, `decision.source` is the latest item, `share_chance == engine.share_chance("v1", source)`); `test_routine_moves_come_in_batches_of_five_across_the_window` (12 villagers: groups of 5, 5, 2 with gaps of at least `spread / 3`); `test_phases_stay_evenly_spaced_with_a_routine`.
- [ ] **Step 2: Run** `python -m pytest examples/rumor_mill/tests/test_game.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite and ruff. Expected: PASS.
- [ ] **Step 5: Commit** `Move villagers through their day and let them carry news as they go`.

---

### Task 9: Play it and tune it

- [ ] Real-model session against a local Contex on the 50-villager seed. Bars from the spec: the Aldwin/Alicia market-tax exchange keeps going past one back-and-forth and pulls in a neighbour; a whisper to a villager who then walks their round spreads to a second place; a reply after a brush-off lands on the hinted subject at least once.
- [ ] Report numbers: lines per scene, scenes per game day, bystanders joined per scene, Jev requests and Haiku lines per scene, routine moves per part of the day, carried shares per day, cost per 5 minutes.
- [ ] Tune only the Task 1 and Task 8 constants and the six Config values; one commit per module changed, each with the numbers before and after. Leave the game running for Rob with the stop command.

---

## Self-Review

- **Spec coverage:** A1 exchange start for both kinds of talk and `max_scenes` (Task 2), topic as the whisper or the opening line (Tasks 1, 2), momentum from heat plus the Score plus bonuses minus decay (Task 1), addressing and `scene_cooldown_seconds` (Task 2), a scene turn is a normal turn and leaving drops momentum (Tasks 1, 2), drawing in at momentum × `join_rate` with a cap of 6 (Tasks 1, 3), four endings with the veered sentence (Tasks 1, 2), card block and village-talk block with every line heard (Tasks 3, 4), cost (Global Constraints). A2 reply threads, `hint_margin`, two tries, suspicion per message (Task 5), near misses and the lowered query floor (Task 6), subject-naming hints (Task 5). A3 work and leisure places cached in the sidecar, newcomers on arrival (Task 7), phase moves in batches, chosen moves held, the carry rule, no publishing of moves (Task 8). Tuning (Task 9).
- **Rulings on spec gaps:** a player-rooted scene's on-topic check is against the whisper, a village scene's against its opening line (published as a hidden topic card); "the room, for speak" means the room hears the line as today and only participants and drawn bystanders are addressed; "at most two tries per thread" means two whispers (the brushed-off one and one reply); a near miss's thread need is the need it nearly cleared; villagers start the game at their morning place; the page does not switch the composer to "Reply" for a brush-off thread (the card shows the threading).
- **Types:** `Scene`, `Scenes.of/holding/start/join/speaking/said/addressed/turned/end`, `Decision.keen`, `TurnView.ask_keen`, `Rumors.add_topic/carries`, `Feed.on_scene`, `scene` on hop and overheard events, `check(..., hint_need)`, `Routine.load/add/place` are named the same in every task that uses them.
