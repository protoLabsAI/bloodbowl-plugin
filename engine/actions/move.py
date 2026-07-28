"""The Move action: one square at a time, with Standing Up, Dodging and Rushing.

Modelled from the S3 text, with the passages that decide each branch quoted where
they apply. Four of them differ from what an older edition would say, and they are
the reason this was looked up rather than recalled:

* Rushing (S3's name for what used to be Going For It) is 2+, up to twice per
  activation, and **the Rush roll comes FIRST** when a square needs both a Rush
  and a Dodge: "If, when a player attempts to Rush, there would be multiple rolls
  for moving into the square, such as having to Dodge, then the roll for attempt
  to Rush will always come first."
* A Dodge is modified by who Marks the square being moved INTO — not the square
  being left. Leaving a Marked square is what makes a Dodge necessary; it does not
  add a modifier.
* A failed Dodge still MOVES the player: "The player is moved into the square they
  attempted to Dodge into and then Falls Over."
* Standing up costs 3 squares of Move Allowance; a player with MA 2 or less rolls
  a D6 instead, standing on 4+ using their full Move Allowance and, on 1-3,
  remaining Prone with their activation immediately over.

One square per command, deliberately. A coach moves a step, sees the dice, and
decides the next one — which is how the game is actually played, and it keeps
every roll individually attributable in the log.
"""

from __future__ import annotations

from ...pitch import in_bounds
from ..dice import roll_target
from ..events import Event
from ..rules import (
    MAX_RUSHES,
    STAND_UP_COST,
    STAND_UP_ROLL,
    adjacent,
    agility_target,
    dodge_modifier,
    is_marked,
    markers_of_square,
)
from ..skills import SkillContext, apply_value_hook, hooks_for, unmodelled_skills
from ..state import Match
from . import Legality, Outcome, register


def _player_and_side(match: Match, cmd: dict):
    return match.by_id(str(cmd.get("player") or "")), match.clock.active


def validate(match: Match, cmd: dict) -> Legality:
    """May this player step onto this square? Answers without rolling anything,
    so a coach can ask about every square before committing to one."""
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.place != "pitch":
        return Legality(False, f"{p.player.position} is in the {p.place.replace('_', ' ')}, not on the pitch")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    if p.down == "stunned":
        return Legality(False, "a Stunned player cannot act; they recover to Prone at the end of the turn")

    try:
        x, y = int(cmd["x"]), int(cmd["y"])
    except (KeyError, TypeError, ValueError):
        return Legality(False, "x and y are required")
    if not in_bounds(x, y):
        return Legality(False, f"({x},{y}) is off the pitch")
    if not adjacent(p.x, p.y, x, y):
        return Legality(False, "a Move goes one square at a time, to an adjacent square")
    if match.at(x, y) is not None:
        return Legality(False, f"({x},{y}) is occupied")

    # Cost of the step, including standing up first if Prone.
    stand_cost = 0
    if p.down == "prone":
        ctx = SkillContext(match=match, player=p, value=STAND_UP_COST)
        stand_cost = apply_value_hook("stand_up_cost", ctx, p)
    budget = p.movement()
    used = p.ma_used + stand_cost
    rushes_needed = max(0, (used + 1) - budget)
    if rushes_needed > MAX_RUSHES:
        return Legality(
            False,
            f"out of movement: {p.player.position} has MA {budget}, has used {p.ma_used}"
            + (f" and needs {stand_cost} to stand up" if stand_cost else "")
            + f", and may Rush at most {MAX_RUSHES} times",
        )

    detail = {
        "rush": rushes_needed > 0,
        "stand_up_cost": stand_cost,
        "dodge": is_marked(match, p),
        "dodge_modifier": dodge_modifier(match, p, x, y),
        "markers_of_target": [m.id for m in markers_of_square(match, p.side, x, y)],
    }
    return Legality(True, "", detail)


def _stand_up(match: Match, p, dice, events: list[Event]) -> tuple[bool, int]:
    """Returns (still able to act, movement spent). A low-MA player may fail and
    lose the activation entirely."""
    ctx = SkillContext(match=match, player=p, value=STAND_UP_COST)
    cost = apply_value_hook("stand_up_cost", ctx, p)
    if p.movement() <= 2 and cost > 0:
        r = roll_target(dice, "stand up", STAND_UP_ROLL, note="MA 2 or less")
        if not r.passed:
            events.append(
                Event(
                    kind="note",
                    actor=p.id,
                    rolls=[r],
                    text=f"{p.player.position} fails to stand up and their activation ends.",
                )
            )
            return False, 0
        events.append(
            Event(
                kind="player_stood_up",
                actor=p.id,
                detail={"ma_used": p.movement()},
                rolls=[r],
                text=f"{p.player.position} stands up, using their whole Move Allowance.",
            )
        )
        return True, p.movement()
    events.append(
        Event(
            kind="player_stood_up",
            actor=p.id,
            detail={"ma_used": p.ma_used + cost},
            text=f"{p.player.position} stands up"
            + (" for free (Jump Up)." if cost == 0 else f", spending {cost} squares.")
            + "".join(f" {n}." for n in ctx.notes if "Jump Up" not in n),
        )
    )
    return True, cost


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    x, y = int(cmd["x"]), int(cmd["y"])
    events: list[Event] = []
    unmodelled = unmodelled_skills(p)

    # 1. Stand up, if Prone. Must happen before anything else.
    spent = 0
    if p.down == "prone":
        able, spent = _stand_up(match, p, dice, events)
        if not able:
            return Outcome(ok=False, events=events, text=events[-1].text, unmodelled=unmodelled)

    used = p.ma_used + spent
    needs_rush = (used + 1) > p.movement()
    needs_dodge = is_marked(match, p)

    # 2. Rush FIRST when a square needs both rolls — the S3 ordering.
    if needs_rush:
        r = roll_target(dice, "Rush", 2)
        if not r.passed:
            events.append(
                Event(
                    kind="player_moved",
                    actor=p.id,
                    detail={"x": x, "y": y, "ma_used": used + 1},
                    text=f"{p.player.position} Rushes into ({x},{y})…",
                )
            )
            events.append(
                Event(
                    kind="player_fell",
                    actor=p.id,
                    detail={"down": "prone"},
                    rolls=[r],
                    text=f"…trips and Falls Over. {r.describe()}",
                )
            )
            return Outcome(
                ok=False,
                events=events,
                turnover=True,
                text=f"{p.player.position} failed the Rush and Falls Over in ({x},{y}) — turnover.",
                unmodelled=unmodelled,
            )
        events.append(Event(kind="note", actor=p.id, rolls=[r], text=f"Rush succeeds. {r.describe()}"))

    # 3. Dodge, if leaving a Marked square.
    if needs_dodge:
        modifier = dodge_modifier(match, p, x, y)
        # An opponent's Skill can modify our roll, so the hook is asked of the
        # players Marking the destination rather than of the one dodging.
        ctx = SkillContext(match=match, player=p, value=modifier)
        for marker in markers_of_square(match, p.side, x, y):
            for skill, fn in hooks_for("opponent_dodge_modifier"):
                if marker.has_skill(skill):
                    fn(ctx)
        modifier = ctx.value

        r = roll_target(dice, "Dodge", agility_target(p), modifier)
        # Both attempts are kept. A log that shows only the successful re-roll
        # hides the failure that forced it, and the coach reads this log.
        dodge_rolls = [r]
        if not r.passed:
            # The Dodge Skill is once per TURN, not per activation, so the flag
            # lives on the player and is cleared when their turn starts.
            reroll = SkillContext(match=match, player=p, flags={"dodge_reroll_used": p.dodge_reroll_used})
            for skill, fn in hooks_for("dodge_reroll"):
                if p.has_skill(skill):
                    fn(reroll)
            if reroll.flags.get("may_reroll"):
                p.dodge_reroll_used = True
                r = roll_target(dice, "Dodge (re-roll)", agility_target(p), modifier, note="Dodge skill")
                dodge_rolls.append(r)

        if not r.passed:
            # A failed Dodge still moves the player — they land in the square and
            # fall there, which matters for where the ball ends up.
            events.append(
                Event(
                    kind="player_moved",
                    actor=p.id,
                    detail={"x": x, "y": y, "ma_used": used + 1},
                    text=f"{p.player.position} Dodges toward ({x},{y})…",
                )
            )
            events.append(
                Event(
                    kind="player_fell",
                    actor=p.id,
                    detail={"down": "prone"},
                    rolls=dodge_rolls,
                    text=f"…and Falls Over. {r.describe()}",
                )
            )
            return Outcome(
                ok=False,
                events=events,
                turnover=True,
                text=f"{p.player.position} failed the Dodge into ({x},{y}) and Falls Over — turnover.",
                unmodelled=unmodelled,
            )
        events.append(Event(kind="note", actor=p.id, rolls=dodge_rolls, text=f"Dodge succeeds. {r.describe()}"))

    events.append(
        Event(
            kind="player_moved",
            actor=p.id,
            detail={"x": x, "y": y, "ma_used": used + 1},
            text=f"{p.player.position} moves to ({x},{y}).",
        )
    )
    return Outcome(
        ok=True,
        events=events,
        text=f"{p.player.position} moves to ({x},{y}).",
        unmodelled=unmodelled,
    )


register("move", validate, resolve)
