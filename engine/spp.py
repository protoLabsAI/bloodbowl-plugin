"""Star Player Points, which are earned DURING a game even though they are spent
after one.

    "At the end of each game you should add any SPP that a player has earned to
     their tally … so IT'S IMPORTANT TO KEEP TRACK OF EVERY TIME A PLAYER DOES
     SOMETHING THAT GENERATES SPP DURING A GAME."

That sentence is why this is in scope. Spending them is Post-game and needs a
league; earning them is a rule about the pitch, and the engine watches the pitch.

    COMPLETION      "When a player makes a Pass Action that results in an ACCURATE
                     Pass, and is SUCCESSFULLY CAUGHT BY A TEAM-MATE WITHOUT THE
                     BALL HITTING THE GROUND and Bouncing … 1 SPP."
    THROW TEAM-MATE  "…results in a SUPERB Throw, and the thrown team-mate
                     SUCCESSFULLY LANDS ON THEIR FEET, the player who made the
                     Throw Team-mate Action gains 1 SPP. Additionally, if a player
                     is thrown by a team-mate and SUCCESSFULLY LANDS SAFELY, they
                     will ALSO gain 1 SPP for landing safely, REGARDLESS of the
                     type of throw."
    INTERCEPTION    "…the player that made the Interception will gain 2 SPP."
    CASUALTY        "If a player is KNOCKED DOWN AS A RESULT OF A BLOCK ACTION and
                     suffers a Casualty, REGARDLESS OF WHICH PLAYER PERFORMED THE
                     BLOCK ACTION, then the player that knocked them down is said
                     to have caused a Casualty … 2 SPP … even if both players were
                     Knocked Down … and even if the Casualty is RECOVERED by any
                     means."
    TOUCHDOWN       "…they are awarded 3 SPP."
    MVP             "…each Coach NOMINATES SIX PLAYERS on their team that played
                     during the match and assigns them a number between 1 and 6.
                     They then ROLL A D6 … The player that is given the MVP award
                     generates 4 SPP."

TWO EXCLUSIONS, both easy to miss and both load-bearing:

* "STAR PLAYERS DO NOT GENERATE SPP AT ALL during the course of a game." They are
  hired for one match; there is nothing to advance.
* A Casualty caused by "other methods, SUCH AS SPECIAL ACTIONS or by INJURY BY THE
  CROWD, do not generate SPP" — a Block Action, and only a Block Action. Which is
  precisely what VIOLENT INNOVATOR overturns: "if an opposition player suffers a
  Casualty as a result of a SPECIAL ACTION this player performed, this player WILL
  earn Star Player Points for causing a Casualty as appropriate."

So the Skill is not decoration once the ledger exists — it is the one thing that
turns a Chainsaw's victim into an advancement.
"""

from __future__ import annotations

from .dice import Roll
from .events import Event

COMPLETION = 1
THROW_TEAM_MATE = 1
LANDED_SAFELY = 1
INTERCEPTION = 2
CASUALTY = 2
TOUCHDOWN = 3
MVP = 4

# "Each Coach nominates SIX players on their team that played during the match."
MVP_CANDIDATES = 6


def earns(player) -> bool:
    """ "Star Players DO NOT generate SPP AT ALL during the course of a game." """
    return getattr(player.player, "role", "") != "star" and player.player.role != "star"


def award(match, player, points: int, why: str) -> list[Event]:
    """Record SPP against a player. Returns the event, or [] if they cannot earn."""
    if player is None or not earns(player):
        return []
    ev = Event(
        kind="spp_earned",
        actor=player.id,
        detail={"points": int(points), "why": why},
        text=f"{player.name()} earns {points} SPP for {why}.",
    )
    match.apply(ev)
    return [ev]


def mvp(match, dice) -> list[Event]:
    """The end-of-game award. See the quote above.

    Six nominees per side, numbered 1-6, one D6 each. With fewer than six players
    who took part, the D6 still decides — a roll that lands on an empty slot gives
    the award to nobody, which is what "nominates six players" means when you have
    four left standing.
    """
    out: list[Event] = []
    for side in ("home", "away"):
        played = sorted((p for p in match.players if p.side == side and earns(p)), key=lambda p: p.id)
        if not played:
            continue
        d = dice.d6()
        roll = Roll(kind="MVP", dice=[d], total=d, note=f"{side}, {min(len(played), MVP_CANDIDATES)} nominated")
        dice.rolls.append(roll)
        nominees = played[:MVP_CANDIDATES]
        who = nominees[d - 1] if d <= len(nominees) else None
        if who is None:
            out.append(
                Event(
                    kind="note",
                    rolls=[roll],
                    detail={"side": side, "mvp": ""},
                    text=f"The {side} MVP roll lands on an empty slot — nobody takes the award.",
                )
            )
            match.apply(out[-1])
            continue
        out.append(
            Event(
                kind="spp_earned",
                actor=who.id,
                rolls=[roll],
                detail={"points": MVP, "why": "being named Most Valuable Player"},
                text=f"{who.name()} is the {side} MVP and earns {MVP} SPP. {roll.describe()}",
            )
        )
        match.apply(out[-1])
    return out
