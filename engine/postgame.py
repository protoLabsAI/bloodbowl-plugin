"""The Post-game Sequence — what a match can hand to a league, and where it stops.

    "Coaches should complete the following sequence after a game has finished:
       RECORD OUTCOME AND COLLECT WINNINGS
       UPDATE DEDICATED FANS
       PLAYER ADVANCEMENT
       HIRING, FIRING AND TEMPORARILY RETIRING
       EXPENSIVE MISTAKES
       PREPARE FOR NEXT FIXTURE"

Only the first two are about the match that just finished; the other four are about
a TEAM that persists between games, which needs a Treasury, a Team Draft List and a
league to play in. This engine plays exhibition matches from a practice board, so
it does the first two and reports the rest — the same shape the Pre-game Sequence
takes, and for the same reason: the rulebook itself scopes them.

    RECORD OUTCOME: "Each Coach will need to record the outcome of the game … The
     result, either Win, Loss or Draw · How many TOUCHDOWNS each team scored · How
     many CASUALTIES each team caused, COUNTING ONLY THOSE THAT EARNED STAR PLAYER
     POINTS · How many League Points were earned."

That third line is why this module exists at all rather than being a paragraph in
the docs: "counting only those that earned Star Player Points" means the casualty
count is not the number of players in the Casualty box. It is the number of
Casualties caused by a Block Action, by a player who is not a Star — which is
exactly what `engine/spp.py` already records, and nothing else in the engine knows.

    UPDATE DEDICATED FANS: "If your team WON, roll a D6. If the result is EQUAL TO
     OR HIGHER THAN your team's Dedicated Fans Characteristic, INCREASE [it] by 1.
     This can never go above 7. If your team LOST, roll a D6. If the result is
     LOWER THAN your team's Dedicated Fans Characteristic, REDUCE [it] by 1. This
     can never go below 1. If your team DREW, [it] remains the same."

Note the asymmetry, which is easy to get backwards: a WINNER wants a HIGH roll and
a LOSER also wants a high roll. Both are rolling to avoid the bad outcome.
"""

from __future__ import annotations

from .dice import Roll
from .events import Event

MAX_DEDICATED_FANS = 7
MIN_DEDICATED_FANS = 1


def outcome(match) -> dict:
    """The first step, filled in from the match. See the quote above."""
    home, away = int(match.score.get("home", 0)), int(match.score.get("away", 0))
    result = {"home": "draw", "away": "draw"}
    if home != away:
        winner = "home" if home > away else "away"
        result = {winner: "win", match.opponent(winner): "loss"}
    # "counting ONLY those that earned Star Player Points" — so the ledger is the
    # source, not the Casualty box.
    caused = {"home": 0, "away": 0}
    for e in match.events:
        if e.kind != "spp_earned" or (e.detail or {}).get("why") != "causing a Casualty":
            continue
        who = match.by_id(e.actor)
        if who is not None:
            caused[who.side] += 1
    return {
        "result": result,
        "touchdowns": {"home": home, "away": away},
        "casualties": caused,
        "spp": dict(match.spp),
    }


def update_dedicated_fans(dice, before: int, result: str) -> tuple[int, Roll | None]:
    """The second step. Returns (after, the roll) — see the quote above.

    A draw rolls nothing, which is why the Roll is optional rather than a roll
    whose result is thrown away: a die that never mattered should not be in the log.
    """
    if result == "draw":
        return before, None
    d = dice.d6()
    if result == "win":
        after = min(MAX_DEDICATED_FANS, before + 1) if d >= before else before
        note = f"win: {d} vs {before} Dedicated Fans"
    else:
        after = max(MIN_DEDICATED_FANS, before - 1) if d < before else before
        note = f"loss: {d} vs {before} Dedicated Fans"
    return after, Roll(kind="Dedicated Fans", dice=[d], total=d, target=before, passed=after != before, note=note)


def steps(league: bool = False) -> list[dict]:
    """The six steps, each saying whether this engine runs it."""
    return [
        {
            "step": "Record Outcome and Collect Winnings",
            "applies": True,
            "why": "The result, Touchdowns, "
            "Casualties that earned SPP, and every player's SPP — all of it is in the match.",
        },
        {"step": "Update Dedicated Fans", "applies": True, "why": "A D6 against the team's Dedicated Fans."},
        {
            "step": "Player Advancement",
            "applies": bool(league),
            "why": "Spending SPP needs a team that persists "
            "between games. The SPP themselves are recorded — see `engine/spp.py`.",
        },
        {
            "step": "Hiring, Firing and Temporarily Retiring",
            "applies": bool(league),
            "why": "Needs a Treasury and a Team Draft List. This is where Plague Ridden's new Lineman would be "
            "hired permanently; the engine has already put them in the Reserves Box.",
        },
        {"step": "Expensive Mistakes", "applies": bool(league), "why": "A Treasury to lose gold from."},
        {"step": "Prepare for Next Fixture", "applies": bool(league), "why": "There is no next fixture."},
    ]


def report(match, dice, dedicated: dict | None = None) -> dict:
    """Run the Post-game Sequence and record it. Returns what a league would need.

    Emitted as events like everything else, so a match's log carries its own
    aftermath rather than a caller having to remember to ask.
    """
    got = outcome(match)
    fans = {}
    for side in ("home", "away"):
        before = int((dedicated or {}).get(side, 1))
        after, roll = update_dedicated_fans(dice, before, got["result"][side])
        fans[side] = after
        if roll is not None:
            dice.rolls.append(roll)
        match.apply(
            Event(
                kind="post_game",
                detail={"side": side, "dedicated_fans": after, "was": before, "result": got["result"][side]},
                rolls=[roll] if roll is not None else [],
                text=f"{side} {got['result'][side]}: Dedicated Fans "
                + (f"{before} → {after}." if after != before else f"stay at {before}.")
                + (f" {roll.describe()}" if roll is not None else ""),
            )
        )
    return {**got, "dedicated_fans": fans, "steps": steps(league=False)}
