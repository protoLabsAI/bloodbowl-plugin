"""The Pre-game Sequence, and the two Inducements a match can actually use.

    "With the game set up, the first thing that is required is to run through the
     Pre-game Sequence. There are FIVE SIMPLE STEPS to the Pre-game Sequence,
     however, SOME OF THESE ARE ONLY RELEVANT TO LEAGUE PLAY … The Pre-game
     Sequence is as follows:
       THE FANS: Coaches determine how many of their team's fans have come…
       THE WEATHER: … coaches will need to determine what the weather is like…
       TAKE ON JOURNEYMEN: This step is ONLY USED IN LEAGUE PLAY…
       INDUCEMENTS: This step is ONLY USED IN LEAGUE PLAY…
       DETERMINE KICKING TEAM: Coaches will roll off to determine who is the
        kicking team (defence) and who is the receiving team (offence)."

THE RULEBOOK ITSELF SCOPES TWO OF THE FIVE OUT of a non-League game, in those
words, and this engine plays exhibition matches from a practice board. So the
sequence is COMPLETE here at three steps, and steps 3 and 4 are reported as
League-only rather than as gaps — the difference matters, because "not modelled"
would suggest something is missing that the rules ask a game like this to do.

    THE FANS: "To work out your Fan Factor, you must first see how many
     Fair-weather Fans are cheering for your team by ROLLING A D3. Next, ADD the
     number of Fair-weather Fans to your DEDICATED FANS CHARACTERISTIC as found on
     your Team Roster. The total number is your Fan Factor…"

    DEDICATED FANS: "When you draft a team, it will AUTOMATICALLY have a Dedicated
     Fans Characteristic of 1 … you may improve [it] up to a maximum of 3."

So Fan Factor is D3 + Dedicated Fans, and Dedicated Fans is 1 unless somebody paid
for more. It is rolled here rather than defaulted to zero, which is what it used to
be: Pitch Invasion adds it to a D6, and a zero made that event quietly milder than
the rules intend.

BRIBES are the one Inducement a match can use without a League to buy them in,
because the Kick-off Event Table hands them out for free:

    GET THE REF: "Each team immediately receives ONE FREE BRIBE Inducement. This
     Bribe must be used by the end of the game or it is lost."

    BRIBES: "When a player is Sent-off, AFTER ANY ATTEMPT TO ARGUE THE CALL HAS
     BEEN MADE, you may use a Bribe so long as you are still able. When a Bribe is
     used, roll a D6. On a 2+, the player is NOT Sent-off (AND NO TURNOVER IS
     CAUSED). On a NATURAL 1, the referee pockets the Bribe but sends the player
     off anyway — the player is still Sent-off and the Bribe is lost."

Note the order — Argue the Call first, then the Bribe — and note that a successful
Bribe cancels the Turnover, which a successful argument does not. It is strictly
better than arguing, and it is the only thing in the game that undoes a Foul
completely.
"""

from __future__ import annotations

from .dice import Roll
from .events import Event

# "When you draft a team, it will automatically have a Dedicated Fans
# Characteristic of 1". The floor, not a guess.
DEFAULT_DEDICATED_FANS = 1


def fan_factor(dice, dedicated: int) -> tuple[int, Roll]:
    """D3 Fair-weather Fans plus the Dedicated Fans Characteristic."""
    d = dice.dn(3)
    total = d + max(0, int(dedicated))
    return total, Roll(
        kind="Fan Factor",
        dice=[d],
        total=total,
        modifier=int(dedicated),
        note=f"D3 Fair-weather +{dedicated} Dedicated",
    )


def steps(league: bool = False) -> list[dict]:
    """The five steps, in order, each saying whether this game runs it.

    Returned rather than printed so `bb_game_new` can report exactly what it did
    and what it skipped — a sequence the engine silently performs three-fifths of
    is one nobody can check.
    """
    return [
        {"step": "The Fans", "applies": True, "why": "Fan Factor is rolled: D3 + Dedicated Fans."},
        {"step": "The Weather", "applies": True, "why": "2D6 on the Weather Table."},
        {
            "step": "Take On Journeymen",
            "applies": bool(league),
            "why": '"This step is only used in League Play" — and this is an exhibition match.',
        },
        {
            "step": "Inducements",
            "applies": bool(league),
            "why": '"This step is only used in League Play." The one exception is a Bribe from '
            "Get the Ref, which the Kick-off Event Table hands out for free.",
        },
        {"step": "Determine Kicking Team", "applies": True, "why": "A roll-off, unless a side was named."},
    ]


def bribe(match, player, dice, rec) -> bool:
    """Spend a Bribe on a sending-off. Returns True if the player stays on.

    "AFTER any attempt to Argue the Call has been made" — so this is asked second,
    and only when arguing failed. Taken whenever one is available: a Bribe kept
    past the final whistle "is lost", so there is never a reason to save the last
    one for a sending-off that may not come.
    """
    side = player.side
    if int(match.bribes.get(side, 0)) <= 0:
        return False
    d = dice.d6()
    roll = Roll(kind="Bribe", dice=[d], total=d, target=2, passed=d >= 2)
    dice.rolls.append(roll)
    rec.emit(
        Event(
            kind="bribe_used",
            actor=player.id,
            detail={"side": side, "worked": bool(roll.passed)},
            rolls=[roll],
            text=(
                f"The {side} Coach slips the referee an envelope. {roll.describe()} "
                + (
                    f"{player.name()} stays on, and no Turnover is caused."
                    if roll.passed
                    else "The referee pockets it and sends them off anyway — the Bribe is lost."
                )
            ),
        )
    )
    if not roll.passed:
        return False
    rec.emit(
        Event(
            kind="player_reinstated",
            actor=player.id,
            detail={"x": player.x, "y": player.y, "bribed": True},
            text=f"{player.name()} is placed back in the square they were in.",
        )
    )
    return True
