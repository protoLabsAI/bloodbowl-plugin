"""The rules engine: match state, an event log, dice, and pluggable actions.

The plugin's thesis, applied to play. Roster data is structured so a stat cannot
be paraphrased into something wrong; the engine is authoritative so a RULING
cannot be. The agent coaches — it decides what to do and explains why — and the
engine decides whether that was legal and what the dice said. A coach that
adjudicates its own dodge rolls will eventually invent one, and unlike a wrong
stat, a wrong ruling changes the game.

Layout:
    dice.py      rolls, recorded; seeded for generation, replayed for re-execution
    events.py    the log — facts, each carrying the dice that produced it
    state.py     Match, PlayerState, and fold() — the replay
    rules.py     shared board maths (tackle zones, assists) used by every action
    actions/     one module per action, each validate() + resolve()
    skills.py    skills as hooks, so a new one is a registration not an edit

Nothing here imports the host. Everything is testable with no server running.
"""

from __future__ import annotations
