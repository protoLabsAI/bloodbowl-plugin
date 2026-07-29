"""Whose move it is, and whether anybody needs telling.

A head-to-head has a gap the practice board never had: after you end your turn,
SOMETHING HAS TO HAPPEN. A human looking at the board can see it is the
opposition's move; an agent cannot see anything unless it is asked a question.

So the engine records the fact — it already did, in `turn_started` and
`choice_pending` — and this works out who is waiting on whom. Publishing that fact
is the SURFACE's job (`registry.emit` in `__init__.py`), not the engine's: rules do
not know what a message bus is, and an engine that imported one could not be tested
without it.

The two things that can be owed are deliberately different:

  A TURN     it is your side's turn and the clock is running.
  AN ANSWER  a Kick-off Event stopped and asked YOUR side something, and until you
             answer nothing else in the game can happen at all — including the
             ball landing.

The second is the more urgent and the easier to miss, because the clock has not
moved and nothing looks wrong.
"""

from __future__ import annotations

from .state import Match


def owed(match: Match) -> dict:
    """What the match is waiting for. ``{}`` when it is waiting for nothing.

    Returns ``{"side", "controller", "why", "half", "turn"}`` — enough for a bus
    payload and for a prompt, without the caller having to reach back into the
    match to build either.
    """
    if match.over or not match.controllers:
        return {}

    # An unanswered question first: it blocks everything, including the clock.
    pending = match.pending or {}
    if pending.get("choice"):
        side = str(pending.get("side") or "")
        who = str(match.controllers.get(side) or "")
        if who:
            return {
                "side": side,
                "controller": who,
                "why": "answer",
                "question": str(pending.get("text") or pending.get("choice") or ""),
                "half": match.clock.half,
                "turn": match.clock.turn,
            }
        return {}

    side = match.clock.active
    who = str(match.controllers.get(side) or "")
    if not who:
        return {}
    return {
        "side": side,
        "controller": who,
        "why": "turn",
        "half": match.clock.half,
        "turn": match.clock.turn,
    }


def changed(before: dict, after: dict) -> dict:
    """``after``, but only if it is somebody NEW being waited on.

    A turn produces many calls and the same side is owed throughout it. Publishing
    on every one would nudge the agent once per action of its own turn, which is
    both useless and expensive — the point is the MOMENT the ball passes over, not
    the state of it having passed.
    """
    if not after:
        return {}
    same = (
        before.get("side") == after.get("side")
        and before.get("why") == after.get("why")
        and before.get("turn") == after.get("turn")
        and before.get("half") == after.get("half")
    )
    return {} if same else after
