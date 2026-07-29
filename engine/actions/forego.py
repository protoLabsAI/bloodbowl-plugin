"""Forego Activation — and the Stalling check that comes with it.

    "Sometimes you may not wish to activate a player on your team, in which case
     they can declare they will forego their activation. In these instances, the
     player will not be activated, and so will not be subjected to any rules that
     take place at the start of or during their activation, WITH THE EXCEPTION of
     seeing if the Crowd Takes Action if the Player is deemed to be Stalling.
     However, once a player has declared they will forego their activation, they
     cannot later be activated in the same turn."

That exception is why these two rules share a module. Foregoing is the one way to
dodge an activation gate — a Bone Head who forgoes never rolls — and the rules
close the loophole for the one check that is about the crowd rather than the
player.

    STALLING: "Should a player be in possession of the ball when they are
     activated, can score a Touchdown WITHOUT HAVING TO ROLL ANY DICE, yet finishes
     their activation without having scored a Touchdown then they are said to be
     Stalling. … To determine if the Crowd Takes Action when a player is Stalling,
     roll a D6 at the end of the Stalling player's activation. If the score on the
     D6 is EQUAL TO OR GREATER THAN the team's current Turn number, then the
     Stalling player is hit by some form of projectile thrown by the crowd and is
     immediately Knocked Down. This will cause a Turnover."

Note which way round that comparison goes. The roll gets SAFER as the half wears
on — stalling on turn 2 is punished on a 2+, stalling on turn 8 only on an 8,
which cannot happen. That is the rule doing what it is for: it stops a team
sitting on the ball early, and lets them run the clock down at the end, which is
the tactic the section opens by calling legitimate.

    "This roll must still be taken if a Player Forgoes their Activation."
"""

from __future__ import annotations

from ..dice import Roll
from ..events import Event
from ..injury import knock_down
from ..rules import could_score_without_dice
from ..state import Match
from . import Legality, Outcome, Recorder, ended, register


def stalling_check(match: Match, p, dice, rec: Recorder) -> bool:
    """Roll for the crowd if this player is Stalling. Returns True on a turnover."""
    if not could_score_without_dice(match, p):
        return False
    turn = match.clock.turn
    d = dice.d6()
    roll = Roll(kind="Crowd", dice=[d], total=d, target=turn, passed=d >= turn, note=f"Stalling on turn {turn}")
    dice.rolls.append(roll)
    rec.emit(
        Event(
            kind="note",
            actor=p.id,
            rolls=[roll],
            detail={"stalling": True},
            text=f"{p.name()} could have scored and did not — the crowd notices. {roll.describe()}",
        )
    )
    if d < turn:
        return False
    rec.emit(
        Event(
            kind="note",
            actor=p.id,
            text=f"The crowd pelts {p.name()} with whatever came to hand.",
        )
    )
    rec.absorb(knock_down(match, p, dice, cause="hit by the crowd"))
    return True


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    if p.done:
        return Legality(False, f"{p.name()}'s activation is already over")
    return Legality(True, "", {"stalling": could_score_without_dice(match, p)})


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    rec = Recorder(match)
    # The Stalling roll comes FIRST: the player must still be on their feet and
    # holding the ball for the question to mean anything, and ending the
    # activation is what makes them "finish without having scored".
    turned_over = stalling_check(match, p, dice, rec)
    rec.emit(ended(p.id, "forego", f"{p.name()} foregoes their activation."))
    return Outcome(
        ok=not turned_over,
        events=rec.events,
        turnover=turned_over,
        text=(
            f"{p.name()} was pelted by the crowd for Stalling — turnover."
            if turned_over
            else f"{p.name()} foregoes their activation."
        ),
    )


register("forego", validate, resolve)
