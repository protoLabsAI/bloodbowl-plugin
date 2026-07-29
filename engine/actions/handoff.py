"""The Hand-off Action: give the ball to an adjacent team-mate, who must Catch it.

Simple next to a Pass — no range, no accuracy, no interception — but it is still a
Catch, so it can still be dropped, and a dropped ball bounces and is a turnover
risk like any other. The receiving player can also score on the catch, since S3
allows a Touchdown from "a player Catching the ball whilst within the opposition
End Zone".
"""

from __future__ import annotations

from ..ball import catch
from ..events import Event
from ..rules import adjacent
from ..skills import unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, refuse_if_spent, register


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    spent = refuse_if_spent(match, p, "handoff")
    if spent:
        return Legality(False, spent)
    if p.place != "pitch" or p.down != "standing":
        return Legality(False, "only a Standing player on the pitch can hand the ball off")
    if match.ball.carrier != p.id:
        return Legality(False, f"{p.name()} is not holding the ball")

    t = match.by_id(str(cmd.get("target") or ""))
    if t is None:
        return Legality(False, f"no target with id {cmd.get('target')!r}")
    if t.side != p.side:
        return Legality(False, "you cannot hand the ball to the opposition")
    if t.place != "pitch":
        return Legality(False, "the target is not on the pitch")
    if not adjacent(p.x, p.y, t.x, t.y):
        return Legality(False, "a Hand-off goes to an ADJACENT team-mate")
    return Legality(True, "", {"target": t.id, "catcher_down": t.down})


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    t = match.by_id(str(cmd["target"]))
    rec = Recorder(match)
    unmodelled = sorted(set(unmodelled_skills(p)) | set(unmodelled_skills(t)))

    rec.emit(
        Event(
            kind="ball_dropped",
            actor=p.id,
            detail={"x": t.x, "y": t.y},
            text=f"{p.name()} hands the ball to {t.name()}.",
        )
    )
    rec.absorb(catch(match, t, dice, team_reroll=bool(cmd.get("team_reroll"))))

    rec.emit(ended(p.id, "handoff", f"{p.name()}'s Hand-off Action is over."))
    held = match.ball.carrier == t.id
    return Outcome(
        ok=held,
        events=rec.events,
        # A dropped hand-off is a turnover — the ball hit the ground on your turn.
        turnover=not held,
        text=(f"{t.name()} takes the hand-off." if held else f"{t.name()} drops the hand-off — turnover."),
        unmodelled=unmodelled,
    )


register("handoff", validate, resolve)
