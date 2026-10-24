# Pressure to Leave Town — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Villagers build up pressure from damaging and frightening news. It shows in Jev's turn and forces them out at 1.0, and the departure is credited to the player most responsible.

**Architecture:**
- A `Pressure` store, owned by the engine, sits alongside `Rapport`.
- `decide` adds two Score questions to the turn's existing Jev request and returns their values on `Decision`.
- The engine applies the change after each turn, decays pressure in the clock loop, forces a departure at the mark, and credits it.

**Spec:** `docs/superpowers/specs/2026-10-23-rumor-mill-pressure-design.md`

## Global Constraints

- Branch `feat/rumor-mill-tiles` (PR #477). Don't push from tasks. No worktree.
- Commits end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E
  ```
- Commit only your own files; never `git add -A`.
- Tests: `/private/tmp/claude-501/-Users-robmiller-Projects-contex/dc5af79c-d8f0-4711-83c2-3ccea8efac2d/scratchpad/venv-web/bin/python -m pytest examples/rumor_mill/tests -q`. Lint: `ruff check examples/rumor_mill`.
- Config values:

  | Field | Value |
  |---|---|
  | `pressure_per_hearing` | 0.3 |
  | `pressure_tie_factor` | 1.5 |
  | `pressure_decay` | 0.05 (per part of the day) |
  | `pressure_shaken` | 0.4 |
  | `pressure_breaking` | 0.7 |
  | `pressure_leave` | 1.0 |

- Sting and fear levels each run none / a little / a lot / devastating, mapped to 0, ⅓, ⅔, 1.
- Breaking words:
  - below `pressure_shaken`: "steady";
  - at least `pressure_shaken`: "shaken";
  - at least `pressure_breaking`: "at the end of their rope".
- Style:
  - no inline imports and no meta comments;
  - concise docstrings;
  - no numbers shown to players.

## Review Focus

1. **A forced departure while a player holds the villager:**
   - storm off first, then leave;
   - the hold must not block it;
   - the dock entry ends.

   Test: `test_forced_departure_overrides_a_hold`.
2. **Credit with no player behind the pressure:** the departure meets nothing. Test: `test_departure_with_no_player_pressure_credits_no_one`.
3. **The tie factor applies only to sting, and only when the source speaker is tied to the villager.** Test: `test_tie_factor_scales_sting_only`.
4. **Decay never goes below 0, and a villager who has left is forgotten.** Test: `test_pressure_decays_and_departed_are_dropped`.
5. **The Score questions are asked only when there is something new to react to:** `view.new` is non-empty. Test: `test_sting_and_fear_asked_only_with_new_lines`.

---

### Task 1: Pressure

**Files:**
- Create `examples/rumor_mill/pressure.py`.
- Modify `config.py`, `decide.py`, `engine.py`, and `effects.py` if crediting needs it.
- Tests: `tests/test_pressure.py`, `tests/test_decide.py`, `tests/test_game.py` or `tests/test_engine.py`.

**Interfaces:**
- `class Pressure` in `pressure.py`, constructed as `Pressure(config)`:
  - `add(vid: str, amount: float, player: str | None) -> float`: adds the amount, tracks each player's share, and returns the new pressure.
  - `level(vid) -> float`.
  - `words(vid) -> str`: the breaking words.
  - `decay() -> None`: lowers every villager's pressure, and their per-player shares in proportion.
  - `culprit(vid) -> str | None`: the player with the largest share.
  - `drop(vid) -> None`.
- `decide.py`:
  - `TurnView.breaking: str | None = None` adds `"how close they are to breaking": breaking` to the state.
  - When `view.new` is non-empty, `_questions` adds Score questions `sting` and `fear`. The instructions are about what the villager just heard, judged from their name, trade, family, standing and fears.
  - `Decision` gains `sting: float | None` and `fear: float | None`, scaled to 0..1 with the existing `_scaled` helper.
- `engine.py`:
  - Owns `self.pressure = Pressure(config)`.
  - In `_turn`, after deciding: `amount = pressure_per_hearing × (sting × (tie_factor if the source speaker is in the villager's relationships else 1) + fear)`. The player is `_player_behind(decision.source)`.
  - Fills `view.breaking` from `pressure.words(vid)` before deciding.
  - Calls `pressure.decay()` in the clock loop on each change of the part of the day.
  - A villager at or above `pressure_leave` has their turn's action forced to LEAVE_TOWN. The hold is ignored, and any holders still get `_storm_off` first.
  - For LEAVE_TOWN, the player reported to `judge.record_action` is `pressure.culprit(vid)`, not the source's player.
  - Calls `pressure.drop(vid)` when a villager is removed.

- [ ] **Step 1:** Write failing tests: the five from Review Focus, plus:
  - `test_decide_returns_sting_and_fear`;
  - `test_breaking_words_reach_the_state`;
  - `test_pressure_at_the_mark_forces_leave_town_and_credits_the_culprit`.
- [ ] **Step 2:** Run them and confirm they fail. **Step 3:** Implement. **Step 4:** Run the full suite and ruff; both should pass.
- [ ] **Step 5:** Commit: "Let pressure build until a villager leaves town".
