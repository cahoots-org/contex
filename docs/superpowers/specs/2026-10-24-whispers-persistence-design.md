# Whispers: Surviving a Deploy — Design

**Status:** approved (Rob, 2026-10-24: "I want both" — keep the village, and keep players' progress)

## Intent

Rob: "Will we be able to update the hosted version of the game without losing the state of the village?" Today every deploy or restart:
- generates a new village;
- starts a new Contex session;
- forgets every run, goal, trophy and rumor.

It also leaves the old village's ~50 projects and subscriptions behind in the shared Contex.

## Goals

- **A redeploy keeps the same village:** the same villagers (including newcomers who arrived since), their places and groups, their memories in Contex, the goal pool and routines. No regeneration cost.
- **A redeploy keeps every player's progress:**
  - runs, goals, met goals, trophies and pending trophy lines;
  - suspicion, rapport, interest and pressure;
  - rumor cards and their spread;
  - the feed history a reload replays;
  - the day's spend toward the cap.

  A player who reloads after a deploy sees what they saw before.
- **No stray data:** nothing is orphaned in Contex by a deploy.

## Non-goals

- Keeping in-flight work: villager turns under way, live conversations, scenes, listens, open sockets, and model calls. These end at the deploy, and the game already copes with each ending.
- Surviving a lost volume or a corrupt save. The game then falls back to a new village, logs it, and carries on.
- Running two copies at once, or more than one replica.

## Step 1: keep the village

- **`WHISPERS_DATA_DIR`** (default: the working directory) holds `whispers_worlds/`, the shared baselines file, and the save file below. On Railway it is a volume mounted at `/data`.
- **`WHISPERS_SESSION` fixed on Railway.** With a fixed session and a saved village at `whispers_worlds/<session>.json`, a start loads that village instead of generating one. This is the existing `load_or_generate` path, with the default seed path derived from the session.
- **The image runs as root only long enough** to give the non-root user the volume (Railway mounts volumes as root). An entrypoint does `chown -R whispers /data` when `/data` exists, then drops to `whispers` (`setpriv`) and runs the game.
- **Railway settings:**
  - deployment overlap 0, so two copies never drive the same villagers;
  - draining 30 seconds, so the game has time to save on SIGTERM.

## Step 2: save and restore the game

### Saving
- **One file:** `whispers_worlds/<session>.state.json`. It holds `{"version": 1, "saved_at": <wall clock>, "session": ..., <component>: {...}}`.
- **Written atomically** (temp file plus rename):
  - every `save_every_seconds` (Config, default 60) while the game runs;
  - on SIGTERM or SIGINT, before shutdown.
- **Each stateful class owns its shape**, through `to_state() -> dict` and `restore(state: dict) -> None`. The JSON stays plain: sets become lists, tuple keys become lists of `[key parts..., value]`, dataclasses become dicts, datetimes become ISO strings. No pickle.
- **Monotonic timestamps** (`engine._read_at`, `_last_turn`) are not saved; they are transient. Wall-clock timestamps are saved as they are.

### What is saved

This is from the state inventory: everything classed DURABLE.
- **The `Town`:** living villagers in full (newcomers included), places, groups with members, departed villagers.
- **`Ids` counters, `Lineage`, `Facts`** (in order, capped as today).
- **`Feed`:** recent events, the next event id, `_closed`, `_resolved`, `_answered`, `_listening`.
- **`Game`:** day, phase, elapsed fraction of the phase, recent moves.
- **`Usage`:** tokens, per-player spend, today's spend and its day.
- **`Suspicion`** (scores and evidence), **`Rapport`** (rapport, interest, spoken), **`Pressure`** (levels and shares).
- **`Engine`:** `_gossip`, `_kept`, `_traced`, `_denied`.
- **`Rumors`:** cards, with on restore every `frontier[*].scene` and every non-null `until` cleared, and scene marks dropped. Scenes don't survive.
- **`Players`:** every run with its durable fields, plus `cases` and `pending`. On restore a run has no sockets, so it is inactive until its player reconnects.
- **`Judge`:** `world`, `world_since`, `whisperers`, `_acted`.
- **`Hints`:** the next key and `_hinted`.

### Restoring
- **Restore only when the save file exists, its `version` matches, and its `session` matches.** Then restore in the inventory's order:
  1. Town;
  2. Ids, Lineage, Facts;
  3. Feed;
  4. Game, Usage;
  5. Suspicion, Rapport, Pressure, Engine fields;
  6. Rumors;
  7. Overhear evidence for every live goal and the world goal (republish), and baselines for their claims;
  8. Players, Judge, Hints;
  9. then add villagers (which resumes their Contex subscriptions under the same session), and start the engine, judge and web as today.
- **Counters resume past every id in use:** Ids, feed event ids, hint keys and group ids. Take the larger of the saved counter and the largest id seen.
- **A save that can't be used** (wrong version or session, or unreadable): log it, keep the village (step 1), and start the game state fresh.
- **Tabs reconnect.** The `boot` token changes on restart, so open tabs reload and replay the restored feed.

## Testing
- Each class round-trips: `restore(to_state())` gives an equal object, via a test per class.
- **A whole-game round trip:**
  1. a scripted game (stub Contex, Jev and writer) has a player whisper, villagers retell, a goal progress and a trophy land;
  2. save;
  3. build a fresh game from the same files;
  4. restore.

  After that, `/api/state` for that player matches what it was before, and new ids don't collide with restored ones.
- SIGTERM writes the save (unit-test the handler without killing the process).
- Version or session mismatch falls back to a fresh game state with the same village.

## Left to the builder
- Where the per-class methods live (on the class, or a small module that knows each class).
- Exact JSON shapes.
- Wiring the periodic save task.
