"""Team Re-rolls: what may be re-rolled, by whom, and at what cost.

Quoted from the S3 source, because two of these are not what recall would say.

    "A team will always start the game with its full amount of Team Re-rolls and
     any used during the first half of a game will be REPLENISHED AT HALF-TIME.
     This means that a team will always start each half of a game with its full
     complement of Team Re-rolls. UNUSED TEAM RE-ROLLS DO NOT CARRY OVER to the
     next half."

    "Team Re-rolls can only be used when the team is ACTIVE. When a Team Re-roll
     is used to re-roll a dice pool, ALL THE DICE IN THE POOL must be re-rolled. A
     Team Re-roll cannot be used to re-roll any of the following types of roll:
     Scatter, Armour, Injury, Casualty, Throw-in, Bribe, Argue the Call or if the
     Crowd Takes Action. Team Re-rolls can never be used to re-roll an opposing
     Coach's dice."

    "A COACH MAY USE AS MANY TEAM RE-ROLLS AS THEY WANT DURING THEIR TURN, though
     they may still NEVER RE-ROLL A RE-ROLL."

That last sentence is the one to read twice. A previous edition allowed one Team
Re-roll per team turn and that is what most people will tell you; S3 does not cap
them at all. The only limits are how many you bought, and that a die which is
already a re-roll cannot be re-rolled again.

    LONER (X+): "Whenever this player wishes to use a Team Re-roll, they must roll
     a D6. If they roll equal to or higher than the number shown in brackets, then
     they may use the Team Re-roll as normal. If they roll lower … they may not
     re-roll the dice and THE TEAM RE-ROLL IS LOST just as if it had been used."

HOW MANY DOES A TEAM HAVE? That is a drafting decision — a team buys them at a
price on its roster (``reroll_cost``), and this engine starts from a practice
board rather than a drafted team. So the count is an INPUT, defaulted and
reported rather than invented: ``bb_game_new(rerolls=N)``, ``DEFAULT_REROLLS``
otherwise, and the number is in every state report so nobody has to guess what
the engine assumed.
"""

from __future__ import annotations

import re

from .dice import roll_target
from .events import Event

# A middling purchase. Stated rather than derived, because nothing on a practice
# board says how many this team bought — see the module docstring.
DEFAULT_REROLLS = 3

# "A Team Re-roll cannot be used to re-roll any of the following types of roll".
# Matched against the Roll.kind the engine records, so the names here are the
# engine's, and the test that pins them names the rulebook's beside each.
CANNOT_REROLL = (
    "Armour",
    "Injury",
    "Casualty",
    "Scatter",
    "Bounce",
    "Deviate",
    "Throw-in",
    "Bribe",
    "Argue the Call",
)


def excluded(kind: str) -> bool:
    """Is this the kind of roll a Team Re-roll may never touch?"""
    base = kind.split("(")[0].strip()
    return any(base.startswith(x) for x in CANNOT_REROLL)


def available(match, player) -> int:
    """Team Re-rolls this player's side could use right now.

    Zero when it is not their turn: "Team Re-rolls can only be used when the team
    is active", and "can never be used to re-roll an opposing Coach's dice" —
    which is the same rule said twice and worth honouring in one place.
    """
    if player is None or player.side != match.clock.active:
        return 0
    return (
        int(match.rerolls.get(player.side, 0))
        + int(match.drive_rerolls.get(player.side, 0))
        + leader_rerolls(match, player.side)
    )


def leader_rerolls(match, side: str) -> int:
    """LEADER: "A team that has one or more players with this Skill ON THE PITCH at
    the start of a half may gain A SINGLE EXTRA Team Re-roll … A team can only use
    a Leader Re-roll IF THEY HAVE A PLAYER WITH THE LEADER SKILL ON THE PITCH, and
    if ALL players with this Skill are removed from play … before the Leader
    Re-roll is used THEN IT IS LOST."

    A single extra, however many Leaders — and it evaporates the moment the last
    one leaves the pitch, which is why it is computed from the board rather than
    banked as a number. `leader_used` records that it has been spent for the half.
    """
    if match.leader_used.get(side):
        return 0
    if any(p.has_skill("Leader") for p in match.on_pitch(side)):
        return 1
    return 0


def _loner_target(player) -> int | None:
    """The number in Loner's brackets, or None if the player is not a Loner."""
    for raw in player.player.skills or []:
        if raw.split("(")[0].strip().casefold() == "loner":
            m = re.search(r"\d+", raw)
            return int(m.group(0)) if m else 4
    return None


def spend(match, player, kind: str, dice, rec) -> bool:
    """Try to spend a Team Re-roll on a failed ``kind`` roll.

    Returns True if the dice should be rolled again. The re-roll is deducted
    either way when Loner fails, because "the Team Re-roll is LOST just as if it
    had been used" — which is the whole cost of the Trait and the part that would
    be easy to quietly skip.
    """
    if excluded(kind) or not available(match, player):
        return False
    # Which pot it comes out of matters: the Leader Re-roll is not replenished at
    # half-time by the same rule the bought ones are, and it is lost outright if
    # the last Leader leaves. Spend the bought ones first.
    from_leader = (
        int(match.rerolls.get(player.side, 0)) == 0
        and int(match.drive_rerolls.get(player.side, 0)) == 0
        and leader_rerolls(match, player.side) > 0
    )

    loner = _loner_target(player)
    if loner is not None:
        r = roll_target(dice, "Loner", loner, note="to use a Team Re-roll")
        rec.emit(
            Event(
                kind="note",
                actor=player.id,
                rolls=[r],
                text=f"{player.name()} is a Loner and must roll to use a Team Re-roll. {r.describe()}",
            )
        )
        if not r.passed:
            rec.emit(_spent(match, player, kind, wasted=True, leader=from_leader))
            return False

    rec.emit(_spent(match, player, kind, wasted=False, leader=from_leader))
    return True


def _spent(match, player, kind: str, wasted: bool, leader: bool = False) -> Event:
    left = max(0, available(match, player) - 1)
    return Event(
        kind="team_reroll_used",
        actor=player.id,
        detail={"side": player.side, "on": kind, "wasted": wasted, "left": left, "leader": leader},
        text=(
            f"The Team Re-roll is lost to {player.name()}'s Loner roll — {left} left."
            if wasted
            else f"{player.side} spend a Team Re-roll on the {kind} — {left} left."
        ),
    )
