"""The Secure the Ball Action — new in S3, and the reason recall is untrustworthy
here: an engine written from an older edition simply would not have it.

S3: "Should a player find that the ball has come to a rest on the ground, and
there are no opponents nearby, they may decide to safely pick it up rather than
attempt to do so at speed and risk letting it slip through their fingers."

The terms, from the worked example: it may be declared only when there are no
Standing opposition players within 2 squares OF THE BALL; the player moves into
the ball's square and picks it up on 2+; their activation then ends. "On a 1, the
player fails to pick up the ball and a Turnover is caused - the ball will then
Bounce from the player's square."

So it is a flat 2+ rather than an Agility Test — Tackle Zones cannot modify it,
because the conditions guarantee there are none nearby. That is the whole trade:
give up the rest of the activation, and a Gutter Runner and a Troll pick the ball
up equally well.
"""

from __future__ import annotations

from ..ball import bounce, check_touchdown, standing_opponents_within
from ..dice import roll_target
from ..events import Event
from ..rules import adjacent
from ..skills import unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, refuse_if_spent, register

SECURE_TARGET = 2
SECURE_CLEARANCE = 2  # squares, measured from the BALL


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    if p.place != "pitch" or p.down != "standing":
        return Legality(False, "only a Standing player on the pitch can Secure the Ball")
    spent = refuse_if_spent(match, p, "secure")
    if spent:
        return Legality(False, spent)
    if not match.ball.in_play or match.ball.carrier:
        return Legality(False, "the ball is not loose on the ground")

    bx, by = match.ball.x, match.ball.y
    if not adjacent(p.x, p.y, bx, by) and (p.x, p.y) != (bx, by):
        return Legality(False, "Secure the Ball moves one square onto the ball — stand next to it first")

    near = standing_opponents_within(match, p.side, bx, by, SECURE_CLEARANCE)
    if near:
        return Legality(
            False,
            f"{len(near)} Standing opponent(s) within {SECURE_CLEARANCE} squares of the ball — "
            "Secure the Ball cannot be declared",
        )
    return Legality(True, "", {"target": SECURE_TARGET, "ends_activation": True})


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    rec = Recorder(match)
    unmodelled = unmodelled_skills(p)
    bx, by = match.ball.x, match.ball.y

    if (p.x, p.y) != (bx, by):
        rec.emit(
            Event(
                kind="player_moved",
                actor=p.id,
                detail={"x": bx, "y": by, "ma_used": p.ma_used + 1},
                text=f"{p.name()} steps carefully onto the ball at ({bx},{by}).",
            )
        )

    r = roll_target(dice, "Secure the Ball", SECURE_TARGET)
    if r.passed:
        rec.emit(
            Event(
                kind="ball_picked_up",
                actor=p.id,
                rolls=[r],
                text=f"{p.name()} secures the ball. {r.describe()}",
            )
        )
        rec.absorb(check_touchdown(match, p))
        rec.emit(ended(p.id, "secure", f"{p.name()} has the ball and their activation ends."))
        return Outcome(ok=True, events=rec.events, text=f"{p.name()} secures the ball.", unmodelled=unmodelled)

    rec.emit(
        Event(
            kind="note",
            actor=p.id,
            rolls=[r],
            text=f"{p.name()} fumbles it even unopposed — turnover. {r.describe()}",
        )
    )
    rec.absorb(bounce(match, dice))
    rec.emit(ended(p.id, "secure"))
    return Outcome(
        ok=False,
        events=rec.events,
        turnover=True,
        text=f"{p.name()} failed to secure the ball — turnover.",
        unmodelled=unmodelled,
    )


register("secure", validate, resolve)
