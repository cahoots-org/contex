# Interest and Rapport — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A villager's interest in a rumor rises and falls with what the player says. It tips her toward hearing, believing and acting, or ends the conversation. Her rapport with the player carries across rumors.

**Architecture:** A new `Rapport` store, owned by the engine like `Suspicion`, holds interest per (villager, player, rumor root) and rapport per (villager, player).
- `Player.whisper_to` applies the change and lowers the bar.
- It ends the conversation when patience runs out.
- It feeds the mood into the writer's lines.
- Decide and the judge read interest through the engine.

**Tech Stack:** Python 3.12, asyncio, Jev (decide/judge state), Haiku (`Writer`), pytest with `examples/rumor_mill/tests/helpers.py`.

**Spec:** `docs/superpowers/specs/2026-10-23-rumor-mill-interest-and-rapport-design.md`

## Global Constraints

- Branch `feat/rumor-mill-tiles` (PR #477), with no new PR and no worktree. Tasks don't push.
- Each commit must pass CI and ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E
  ```
- Tests: `/private/tmp/claude-501/-Users-robmiller-Projects-contex/dc5af79c-d8f0-4711-83c2-3ccea8efac2d/scratchpad/venv-web/bin/python -m pytest examples/rumor_mill/tests -q`. Lint: `ruff check examples/rumor_mill`.
- Style:
  - no inline imports and no meta comments;
  - concise docstrings;
  - player-facing text never shows a number for interest or rapport.
- Values, all as Config fields:

  | Field | Value |
  |---|---|
  | `interest_hit` | 0.4 |
  | `interest_near_min` | 0.1 |
  | `interest_near_max` | 0.25 |
  | `interest_brush_off` | −0.35 |
  | `rapport_share` | 0.1 |
  | `interest_end` | −0.6 |
  | `interest_annoyed` | −0.3 |
  | `rapport_lost_patience` | −0.2 |

  - Interest starts at `0.5 × rapport`.
  - The bar drops by `interest × near_miss_band` when interest is positive.
  - Losing patience adds a suspicion bump of `suspicion_whisper_bump / 2`, reason "lost patience with you".

## Review Focus

1. **Interest is keyed by rumor root.** Talking to the same villager about two rumors keeps two interests. Test: `test_interest_is_kept_per_rumor` (Task 1).
2. **A whisper that lands after the bar was lowered** must still be credited as a normal hit, with lineage, cards and goals unchanged. Test: `test_lowered_bar_lands_a_near_miss_at_full_interest` (Task 1).
3. **Lost patience** has to end the thread, the engine conversation and the hold together, so that the next whisper starts a new rumor at rapport's baseline. Test: `test_running_out_of_patience_ends_the_conversation` (Task 1).
4. **Rapport survives across rumors and is per player.** Two players never share it. Test: `test_rapport_is_per_player_and_carries_to_the_next_rumor` (Task 1).
5. **Mood words never leak numbers** to the page or feed. Test: `test_mood_words_have_no_digits` (Task 2).

---

### Task 1: The store, and how a whisper moves it

**Files:**
- Create `examples/rumor_mill/rapport.py`.
- Modify `config.py`, `engine.py` (own `self.rapport = Rapport(config)`), `player.py`, and `writer.py` (`Writer.lose_patience`).
- Test: `tests/test_rapport.py`, `tests/test_player.py`, `tests/test_writer.py`.

**Interfaces:**
- `class Rapport` in `rapport.py`, built as `Rapport(config)`:
  - `interest(vid: str, player: str, rumor: str) -> float`: the value stored for this rumor, or `0.5 × rapport(vid, player)` if none has been stored.
  - `rapport(vid: str, player: str) -> float`.
  - `move(vid, player, rumor, delta: float) -> float`: clamps interest to [−1, 1], moves rapport by `rapport_share × delta` (also clamped), and returns the new interest.
  - `sour(vid, player, amount: float) -> None`: changes rapport only.
  - `forget(vid, player, rumor) -> None`: drops that rumor's interest, so the next start is the rapport baseline.
- `Player.whisper_to`:
  - It works out the rumor key as `rumor or uid`, the same key it uses for `_held`.
  - It passes `bar_drop = max(0, interest) × config.near_miss_band` into `check`. `check` gets a `bar_drop: float = 0.0` parameter and subtracts it from each need's threshold, alongside `hint_need`.
  - After the outcome is known, it calls `engine.rapport.move` with the spec's delta. A near miss interpolates linearly from `interest_near_min` (at the bottom of the band) to `interest_near_max` (at the bar).
  - If the new interest is ≤ `interest_end`, it calls `_lose_patience(target, rumor_key)`. That method:
    - ends the thread, the `_held` entry and the engine conversation (`self.part(vid)` already does all three);
    - applies `rapport.sour(..., rapport_lost_patience)`;
    - adds the suspicion bump;
    - calls `rapport.forget`;
    - gets the line from `writer.lose_patience(villager)` under `charging(self.id)`;
    - emits `feed.on_storm_off(vid, name, line, player=...)`, reusing the existing event and page handling. The reason text is "lost patience with you".
    - No lean-in or brush-off hint is sent for that whisper.
- Remove `THREAD_WHISPERS`, the thread's whisper count, `ANNOYING_MISSES` and `_misses`. `annoyed(vid)` is now `suspicion ≥ warn_threshold or interest(vid, self.id, open rumor) ≤ interest_annoyed`. The open rumor is `self._exchange(vid)`; when there is none, interest doesn't count.
- `continues` on whisper statuses is true unless patience ran out.
- `Writer.lose_patience(villager) -> str` makes one line, at most 80 tokens out, in her temper: "a kindly villager excuses herself, a sour one cuts the newcomer off". It names no newcomer.

- [ ] **Step 1: Write the failing tests:**
  - `test_interest_rises_on_hits_and_near_misses_and_falls_on_brush_offs` (exact deltas from Config)
  - `test_interest_is_kept_per_rumor`
  - `test_lowered_bar_lands_a_near_miss_at_full_interest`
  - `test_running_out_of_patience_ends_the_conversation` (storm_off event, the reason, rapport soured, next whisper starts a new rumor)
  - `test_rapport_is_per_player_and_carries_to_the_next_rumor`
  - `test_a_lean_in_thread_has_no_whisper_cap`
  - `test_annoyed_by_low_interest_or_suspicion`
  - `test_writer_lose_patience_is_one_line_in_temper`

  Update or remove tests that pin the old cap or the two-misses rule. The existing test `test_a_near_miss_on_the_last_try_is_a_brush_off` goes.
- [ ] **Step 2:** Run the new tests and confirm they fail. **Step 3:** Implement. **Step 4:** Run the full suite and ruff; both must pass. **Step 5:** Commit: "Track a villager's interest per rumor and rapport per player".

### Task 2: Lines carry the mood

**Files:** Modify `rapport.py` (word bands), `player.py` (`_hint`, `_go_on`, `_brush_off`), `writer.py` (`go_on`, `brush_off`, `reply`), and `effects.py` (the ANSWER reply). Test: `tests/test_rapport.py`, `tests/test_writer.py`, `tests/test_player.py`.

**Interfaces:**
- `interest_words(x: float) -> str` uses these bands:
  - ≥ 0.5: "eager to hear more"
  - ≥ 0.15: "curious"
  - > −0.15: "lukewarm"
  - > −0.45: "losing interest"
  - otherwise: "about out of patience"
- `rapport_words(x: float) -> str` uses these bands:
  - ≥ 0.4: "fond of the newcomer"
  - ≥ 0.1: "warm toward the newcomer"
  - > −0.1: "neutral toward the newcomer"
  - > −0.4: "cool toward the newcomer"
  - otherwise: "tired of the newcomer"
- `Writer.go_on`, `Writer.brush_off` and `Writer.reply` gain `mood: str = ""`. When set, the prompt gets "How you feel: {mood}." and the instruction to let it set the tone.
- The player builds the mood from `interest_words(interest after the move)`.
  - On the first line of a rumor with this villager (no interest stored before this whisper), the mood also includes `rapport_words` and an instruction to greet the newcomer the way they feel about them.
- In `effects.py`, the ANSWER reply passes the same mood. Engine access to the store: `hub.rapport`, or pass the mood in; use whichever pattern the hub protocol already has.

- [ ] **Step 1:** Write the failing tests:
  - `test_interest_and_rapport_words_bands`
  - `test_mood_words_have_no_digits`
  - `test_writer_lines_carry_the_mood`
  - `test_first_line_of_a_rumor_greets_by_rapport`
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. **Step 4:** Run the full suite and ruff. **Step 5:** Commit: "Let a villager's lines carry how taken they are, and how they feel about you".

### Task 3: Interest tips belief and action

**Files:** Modify `decide.py` (TurnView and state), `engine.py` (fill it in), and `judge.py` (belief state). Test: `tests/test_decide.py`, `tests/test_game.py`, `tests/test_judge.py`.

**Interfaces:**
- `TurnView.taken: str | None = None`: how taken the villager was with the player's whisper behind the turn's source. It is `taken_words(interest)`, with these bands:
  - ≥ 0.5: "eager"
  - ≥ 0.15: "interested"
  - > −0.3: "indifferent"
  - otherwise: "put off"

  It is None when the source roots in no whisper. The engine fills it from `rapport.interest(vid, player_behind(source), lineage.root(source uid))`.
- `decide._state` adds `"how taken they were with the newcomer's story": view.taken` when set.
- In `Judge._believes`, when the best-matching memory roots in a player's whisper, the Jev state gets the same key from that player's interest in that rumor root. The verdict cache key gains the taken word, so a change in eagerness re-asks the question.

- [ ] **Step 1:** Write the failing tests:
  - `test_decide_state_says_how_taken`
  - `test_engine_fills_taken_from_interest`
  - `test_judge_state_says_how_taken_and_rekeys_cache`
- [ ] **Step 2:** Confirm they fail. **Step 3:** Implement. **Step 4:** Run the full suite and ruff. **Step 5:** Commit: "Let how taken a villager was tip their belief and their next move".
