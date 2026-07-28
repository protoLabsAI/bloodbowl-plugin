"""One place where an action is turned into a change to a match.

Tools, HTTP routes and tests all come through here, so the sequence — validate,
resolve, apply, handle the turnover — exists once. When Block and Pass arrive they
plug into the same path rather than each re-deriving what a turnover does.
"""

from __future__ import annotations

from . import actions
from .dice import SeededDice
from .events import Event
from .state import Match, starting_positions

TURNOVER_TEXT = {
    True: "Turnover — the team's turn ends.",
    False: "",
}


def new_match(scenario, seed: int = 0, kicking_to: str = "home") -> Match:
    """Start a match from a set-up board.

    The seed is stored so the match can be regenerated; the log is what lets it be
    re-watched. Both, for the reasons in dice.py.
    """
    m = starting_positions(scenario, seed=seed)
    m.apply(
        Event(
            kind="match_started",
            detail={"kicking_to": kicking_to, "seed": seed},
            text=f"Match begins. {m.home_team or 'Home'} vs {m.away_team or 'Away'}.",
        )
    )
    m.apply(
        Event(
            kind="turn_started",
            detail={"side": kicking_to, "half": 1, "turn": 1},
            text=f"Half 1, turn 1 — {kicking_to} to act.",
        )
    )
    return m


def dice_for(match: Match):
    """A dice source positioned past the rolls already made.

    Re-seeding per action would hand every action the same numbers, so the stream
    is advanced by however many rolls the log already holds. Deterministic, and it
    survives the match being reloaded from disk between actions.
    """
    d = SeededDice(seed=match.seed)
    already = sum(len(e.rolls) for e in match.events)
    for _ in range(already):
        d.d6()
    return d


def act(match: Match, action: str, cmd: dict, dice=None) -> dict:
    """Resolve one action and fold its facts in. Returns a report for the caller."""
    actions.load_all()
    entry = actions.get(action)
    if entry is None:
        return {"ok": False, "error": f"unknown action {action!r}", "actions": actions.names()}
    if match.over:
        return {"ok": False, "error": "the match is over"}

    dice = dice or dice_for(match)
    outcome = entry["resolve"](match, cmd, dice)
    for e in outcome.events:
        match.apply(e)

    if outcome.turnover:
        match.apply(Event(kind="turnover", detail={"side": match.clock.active}, text=TURNOVER_TEXT[True]))
        end_turn(match, forced=True)

    report = outcome.to_dict()
    report["clock"] = match.clock.to_dict()
    report["over"] = match.over
    return report


def end_turn(match: Match, forced: bool = False) -> dict:
    """End the active team's turn.

    Stunned players recover to Prone at the end of a turn — modelled here rather
    than in the clock so the recovery is a recorded fact like everything else.
    """
    for p in match.players:
        if p.down == "stunned" and p.side == match.clock.active:
            match.apply(
                Event(
                    kind="player_placed_prone",
                    actor=p.id,
                    detail={"down": "prone"},
                    text=f"{p.player.position} recovers from Stunned to Prone.",
                )
            )
    was = match.clock.active
    match.apply(
        Event(
            kind="turn_ended",
            detail={"side": was, "forced": forced},
            text=("Turnover ends " if forced else "") + f"{was}'s turn.",
        )
    )
    if not match.over:
        match.apply(
            Event(
                kind="turn_started",
                detail={
                    "side": match.clock.active,
                    "half": match.clock.half,
                    "turn": match.clock.turn,
                },
                text=f"Half {match.clock.half}, turn {match.clock.turn} — {match.clock.active} to act.",
            )
        )
    return {"ok": True, "clock": match.clock.to_dict(), "over": match.over}


def legal_moves(match: Match, player_id: str) -> dict:
    """Every square this player could step to, and what each would cost.

    The anti-confabulation tool. A coach asking this gets the engine's own answer
    for all eight neighbours — which need a Dodge, at what modifier, which need a
    Rush — instead of working it out from a board description and being confidently
    wrong about one of them.
    """
    actions.load_all()
    p = match.by_id(player_id)
    if p is None:
        return {"ok": False, "error": f"no player with id {player_id!r}"}
    validate = actions.get("move")["validate"]
    squares = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            x, y = p.x + dx, p.y + dy
            legal = validate(match, {"player": player_id, "x": x, "y": y})
            entry = {"x": x, "y": y, "legal": legal.ok}
            if legal.ok:
                entry.update(legal.detail)
            else:
                entry["reason"] = legal.reason
            squares.append(entry)
    return {
        "ok": True,
        "player": p.to_dict(),
        "movement_left": max(0, p.movement() - p.ma_used),
        "squares": squares,
    }
