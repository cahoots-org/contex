# Surviving a Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A redeploy keeps the village and every player's progress.

**Spec:** `docs/superpowers/specs/2026-10-24-whispers-persistence-design.md`

**Inventory:** what each class holds, and whether it is durable, rebuildable or transient: `.superpowers/sdd/whispers-persistence/inventory.md`.

## Global Constraints

- **Code:** `examples/whispers`, Python 3.12, stdlib `json` only (no pickle, no new dependencies).
- **Tests:** `/private/tmp/claude-501/-Users-robmiller-Projects-contex/dc5af79c-d8f0-4711-83c2-3ccea8efac2d/scratchpad/venv-web/bin/python -m pytest examples/whispers/tests -q -p no:cacheprovider`. Lint: `ruff check examples/whispers`. Use fake clocks, never real sleeps.
- **Commits:** small and CI-green, only your own files. Each ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01YP7uhbQm8avkDcwdQ9b87E
  ```
- **Style:** concise docstrings, no inline imports, no meta comments, avoid cleverness.
- **Never** use port 8765 or the live site. Never print API keys.
- **Config:**
  - `data_dir: str = ""`: empty means the working directory;
  - `save_every_seconds: float = 60.0`;
  - `STATE_VERSION = 1`.

## Review Focus

1. **Every id space resumes past its largest id in use:** `Ids`, feed event ids, hint keys and group ids. Test: after a restore, the next whisper, event, hint and group get ids above every restored one.
2. **Restore with a missing, unreadable, wrong-version or wrong-session save** keeps the village and starts the game state fresh, without crashing. Test each case.
3. **The save is atomic:** a crash mid-write never leaves a half file that the next start reads. Test: interrupt the write after the temp file is created; the old save is intact.
4. **Restored runs have no sockets,** so the village stays idle until a player reconnects (the activity rule). Test: after a restore, `any_playing()` is false until a socket connects.
5. **No scene survives:** no restored card refers to a scene, and no scene marks remain. Test.

---

### Task 1: Keep the village (step 1)

**Files:** `config.py`, `__main__.py`, `calibration.py` (baselines path under `data_dir`), `town_gen.py` (`world_path` under `data_dir`), `Dockerfile` plus a new `examples/whispers/entrypoint.sh`, `README.md` (the Hosting section), and tests.

- [ ] `world_path(session)` and the baselines path live under `data_dir`.
- [ ] With `WHISPERS_SESSION` set and no `WHISPERS_SEED_PATH`, the village file is `world_path(session)`. If it exists it is loaded; otherwise it is generated and saved there (the existing `load_or_generate`).
- [ ] `entrypoint.sh` runs as root:
  - if `/data` exists, `chown -R whispers:whispers /data`;
  - then `exec setpriv --reuid=whispers --regid=whispers --init-groups python -m examples.whispers --web`.

  The Dockerfile copies it, uses it as the ENTRYPOINT/CMD, and drops the `USER` line (the entrypoint drops privileges instead). Check that `setpriv` exists in python:3.12-slim (util-linux).
- [ ] **README:**
  - mount a volume at `/data` and set `WHISPERS_DATA_DIR=/data` and a fixed `WHISPERS_SESSION`;
  - set deployment overlap to 0 and draining to 30s;
  - a restart keeps the village.
- [ ] **Tests:** paths under `data_dir`; a fixed session reuses an existing village file without calling the writer.
- [ ] **Docker:** build the image; run it with a temporary directory mounted at `/data` and no API keys, overriding the command so it only checks that the user is `whispers` and can write `/data`; then clean up.
- [ ] **Commit:** "Keep a hosted village across restarts".

### Task 2: Small stores save and restore themselves

**Classes:** `Suspicion`, `Rapport`, `Pressure`, `Lineage`, `Ids`, `Facts`, `Game` (elapsed fraction instead of `phase_started`), `Usage`, `Hints`, `Town` (villagers, places, groups, departed).

- [ ] Each class gets `to_state() -> dict` and `restore(state: dict) -> None`, following the spec's JSON rules (sets as lists; tuple keys as lists of parts; dataclasses through `asdict` and a constructor; datetimes as ISO).
- [ ] One round-trip test per class: `restore(to_state())` on a fresh instance compares equal on every durable field.
- [ ] **Commit:** "Let the game's small stores save and restore themselves".

### Task 3: Feed, rumors, players, judge and engine save and restore

- [ ] **`Feed`:** recent events, next event id, `_closed`, `_resolved`, `_answered`, `_listening`.
- [ ] **`Rumors`:** cards, with scene references cleared on restore; a missing `baseline` is fetched again later.
- [ ] **`Players`** and each `Player`:
  - save: durable fields, cases, pending;
  - drop: `_listen_until`, sockets and tasks;
  - restore: rebuild `finished` from the state, and rebuild `_latest` from `_by_id`.
- [ ] **`Judge`:** world, world since, whisperers, acted.
- [ ] **`Engine`:** `_gossip`, `_kept`, `_traced`, `_denied`.
- [ ] One round-trip test per class, plus the Review Focus 4 and 5 tests.
- [ ] **Commit:** "Let the feed, rumors, players, judge and engine save and restore themselves".

### Task 4: Save on a timer and on shutdown, restore on start

- [ ] Produce `save_game(path, components, session) -> None` and `load_game(path, session) -> dict | None` (a new module, `persist.py`):
  - atomic temp-file-plus-rename write;
  - the version and session checks;
  - the components named in the order the spec gives.
- [ ] **In `__main__.py`:**
  - after building the components, if `load_game` returns a state, restore in the spec's order: town, then ids/lineage/facts, then feed, then game/usage, then suspicion/rapport/pressure/engine, then rumors, then overhear republish and baselines for live goals and the world goal, then players/judge/hints;
  - only then add villagers and start the loops;
  - resume every counter past its largest id in use;
  - start a periodic save task;
  - on SIGTERM or SIGINT, save once before shutdown.
- [ ] **Tests:**
  - the Review Focus 1–3 tests;
  - a whole-game round trip: a scripted game with stub Contex, Jev and writer; a player whispers, a villager retells, a goal progresses and a trophy is pending. Then save, build a fresh game from the same files, and restore. `/api/state` for that player equals what it was before, and the next ids don't collide.
  - The SIGTERM handler writes the save (call it directly).
- [ ] **Commit:** "Save the game as it runs and restore it on start".
