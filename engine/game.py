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

    ``kicking_to`` is the RECEIVING side — the team the ball is kicked to, which
    takes the first turn.
    """
    m = starting_positions(scenario, seed=seed)
    m.apply(
        Event(
            kind="match_started",
            detail={"kicking_to": kicking_to, "seed": seed},
            text=f"Match begins. {m.home_team or 'Home'} vs {m.away_team or 'Away'}.",
        )
    )
    start_drive(m, receiving=kicking_to, dice=dice_for(m))
    return m


class _gone:
    """Stands in for a player id that is no longer in the match, so a stale setup
    row cannot crash a kick-off."""

    place = "casualty"


def start_drive(match: Match, receiving: str, dice=None, aim=None) -> list[Event]:
    """Set up, kick off, and hand the first turn to the receiving team.

    Everyone goes back to where they stood at the last kick-off rather than the
    operator being asked to rebuild the board after each score. Casualties stay
    out for the match; a Knocked-out player misses this drive and returns to
    Reserves for the next one, which is the shape of the real End of Drive
    sequence without pretending to model the parts (Apothecary, recovery rolls)
    that are not here.
    """
    from .kickoff import kick

    dice = dice or dice_for(match)
    # The setup is captured ONCE, at the first kick-off, and reused for every
    # later drive. Re-capturing from the current board would bake in wherever the
    # last drive happened to leave everyone, so a team that ended the drive strung
    # out across the pitch would line up that way for the next one.
    #
    # (S3 lets each team set up afresh every drive. Reusing the opening setup is
    # the honest simplification while there is no setup PHASE to do it in — the
    # operator can always rearrange the board and start a fresh match.)
    setup = match.setup or [{"id": p.id, "x": p.x, "y": p.y} for p in match.players if p.place in ("pitch", "reserves")]
    setup = [row for row in setup if (match.by_id(str(row["id"])) or _gone()).place != "casualty"]
    events = [
        Event(
            kind="drive_started",
            detail={"drive": match.drive + 1, "setup": setup, "receiving": receiving},
            text=f"Drive {match.drive + 1}: {receiving} receive.",
        )
    ]
    match.apply(events[0])
    events.extend(kick(match, dice, receiving=receiving))
    events.append(
        Event(
            kind="turn_started",
            detail={"side": receiving, "half": match.clock.half, "turn": match.clock.turn},
            text=f"Half {match.clock.half}, turn {match.clock.turn} — {receiving} to act.",
        )
    )
    match.apply(events[-1])
    return events


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
    # resolve applies its own events (see actions.Outcome) — do not re-apply.
    outcome = entry["resolve"](match, cmd, dice)

    # A Touchdown ends the DRIVE, not just the turn: "As soon as a Touchdown is
    # scored, play stops as a Turnover occurs — however, this is very much a
    # Turnover you can be pleased by! Scoring a Touchdown also marks the end of a
    # Drive." The conceder receives the next kick-off.
    scored = _unresolved_touchdown(match)
    if scored is not None:
        scorer = str(scored.detail.get("side") or match.clock.active)
        end_turn(match, forced=True, start_next=False)
        if not match.over:
            start_drive(match, receiving=match.opponent(scorer), dice=dice)
        report = outcome.to_dict()
        report["clock"] = match.clock.to_dict()
        report["over"] = match.over
        report["touchdown"] = scorer
        return report

    if outcome.turnover:
        match.apply(Event(kind="turnover", detail={"side": match.clock.active}, text=TURNOVER_TEXT[True]))
        end_turn(match, forced=True)

    report = outcome.to_dict()
    report["clock"] = match.clock.to_dict()
    report["over"] = match.over
    return report


def _unresolved_touchdown(match: Match):
    """A Touchdown with no Drive started after it.

    Derived from the log rather than remembered in a side table keyed on the
    match object: a match is reloaded from disk between tool calls, so object
    identity does not survive, and the log is the only thing that does.
    """
    for e in reversed(match.events):
        if e.kind == "drive_started":
            return None
        if e.kind == "touchdown":
            return e
    return None


def end_turn(match: Match, forced: bool = False, start_next: bool = True) -> dict:
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
                    text=f"{p.name()} recovers from Stunned to Prone.",
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
    if match.over or not start_next:
        if match.over:
            match.apply(Event(kind="match_over", text="Full time."))
        return {"ok": True, "clock": match.clock.to_dict(), "over": match.over}
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
    # Blocks the same player could throw, with the arithmetic already done. A
    # coach eyeballing "that looks like a good block" is exactly how you end up
    # handing two dice to a stronger opponent.
    blocks = []
    validate_block = actions.get("block")["validate"]
    for foe in match.on_pitch(match.opponent(p.side)):
        legal = validate_block(match, {"player": player_id, "target": foe.id})
        if not legal.ok:
            continue
        blocks.append(
            {
                "target": foe.id,
                "x": foe.x,
                "y": foe.y,
                "position": foe.player.position,
                **legal.detail,
            }
        )

    # Ball actions this player could take right now, so the view and the coach
    # both learn about Secure the Ball from the engine rather than being expected
    # to remember that S3 added it.
    ball_actions = []
    # Pass targets, with the band and modifier already worked out. A coach
    # eyeballing "that looks catchable" is how a Long Bomb gets thrown by
    # accident — the ruler is measured, not intuited.
    throw = actions.get("pass")
    if throw is not None and match.ball.carrier == player_id:
        for tx in range(1, 16):
            for ty in range(1, 27):
                legal = throw["validate"](match, {"player": player_id, "x": tx, "y": ty})
                if legal.ok:
                    ball_actions.append({"action": "pass", "x": tx, "y": ty, **legal.detail})
    for name in ("secure", "handoff"):
        entry = actions.get(name)
        if entry is None:
            continue
        if name == "handoff":
            for mate in match.on_pitch(p.side):
                legal = entry["validate"](match, {"player": player_id, "target": mate.id})
                if legal.ok:
                    ball_actions.append({"action": name, "target": mate.id, "x": mate.x, "y": mate.y, **legal.detail})
        else:
            legal = entry["validate"](match, {"player": player_id})
            if legal.ok:
                ball_actions.append({"action": name, "x": match.ball.x, "y": match.ball.y, **legal.detail})

    return {
        "ok": True,
        "player": p.to_dict(),
        "movement_left": max(0, p.movement() - p.ma_used),
        "squares": squares,
        "blocks": blocks,
        "ball": match.ball.to_dict(),
        "ball_actions": ball_actions,
    }
