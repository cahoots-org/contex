# Rumor Mill: Pressure to Leave Town — Design

**Status:** approved (Rob, 2026-10-23: "Go straight for it")

## Intent

Rob: "How much have we tested leaving the village? Because I have yet to see an NPC take that action."

Nothing makes a villager leave. Leaving is offered on every turn, but `decide` first drops any action Jev gives less than 10% (`MIN_ACTION_PROBABILITY`), and one piece of news almost never makes leaving for good that likely. Nothing accumulates either. So "drive out" and "Exodus" goals are close to unwinnable.

## Pressure

Each villager has **pressure**, a number from 0 up, owned by the engine like suspicion.

- **It rises on their own turns, at no extra cost.** The Jev request that decides a turn also scores two things about what the villager just heard:
  - **sting**: how badly it damages their name, trade, family or standing (none, a little, a lot, devastating);
  - **fear**: how much it frightens them for their safety or their future here (same four levels).

  Pressure rises by `0.3 × (sting + fear)`, where each score runs from 0 to 1. The sting share is 1.5 times larger when the line came from someone the villager is tied to.
- **It decays** by 0.05 at each part of the day, never below 0. A single rumor fades; a sustained campaign builds.
- **Jev's turn is told how close they are to breaking:**
  - below 0.4: "steady";
  - 0.4 or more: "shaken";
  - 0.7 or more: "at the end of their rope".

  That lifts leaving's odds past the cutoff once the pressure is real. Leaving town is still offered as today.
- **At 1.0 or more they leave on their next turn,** whatever Jev picks. They say a farewell as today. If a player is holding them in conversation, they storm off first, as today. The hold no longer keeps them in town.

## Credit

Pressure remembers how much of it came from each player: the player behind the line that raised it. When the villager leaves, by choice or forced, the departure is credited to the player with the largest share of their current pressure, if any. That feeds `record_action` for "drive out" and "Exodus" goals. This replaces the source-line credit for `leave_town` only.

## Non-goals

- New villager actions.
- Changing other actions' odds or the 10% cutoff.
- Showing pressure on the page. Players see its effects through what villagers say and do.

## Left to the builder

- The Score instructions and level wording.
- Where the store lives: a `Pressure` class, alongside `Rapport`.
- Config names for the values above.
