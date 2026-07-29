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
from .. import rerolls as team_rerolls
from ..ball import check_touchdown, pick_up
from ..dice import roll_target
from ..events import Event
from ..injury import knock_down
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
from ..skills import SkillContext, apply_value_hook, hooks_for, roll_modifier, unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, register


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
        return Legality(False, f"{p.name()} is in the {p.place.replace('_', ' ')}, not on the pitch")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    if p.down == "stunned":
        return Legality(False, "a Stunned player cannot act; they recover to Prone at the end of the turn")
    if p.rooted:
        # Take Root: "Whilst Rooted, a player cannot perform Move Actions … and may
        # not leave their current square for any reason, with the exception of
        # being Knocked Out or suffering a Casualty."
        return Legality(False, f"{p.name()} is Rooted and cannot leave their square")
    if p.done:
        # A Block Action, a Pass, a Hand-off and a Secure the Ball each END the
        # activation, and none of them includes movement afterwards: "may not
        # continue moving after the pass has been made", "their activation
        # immediately ends". This used to go unchecked because the only flag was
        # `acted`, which a single step sets — so a player who Blocked could then
        # stroll away. The Blitz's Block is the one that leaves `done` false.
        return Legality(False, f"{p.name()}'s activation is over")

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
            f"out of movement: {p.name()} has MA {budget}, has used {p.ma_used}"
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


def _stand_up(match: Match, p, dice, rec: Recorder) -> tuple[bool, int]:
    """Returns (still able to act, movement spent). A low-MA player may fail and
    lose the activation entirely."""
    ctx = SkillContext(match=match, player=p, value=STAND_UP_COST)
    cost = apply_value_hook("stand_up_cost", ctx, p)
    if p.movement() <= 2 and cost > 0:
        r = roll_target(dice, "stand up", STAND_UP_ROLL, note="MA 2 or less")
        if not r.passed:
            rec.emit(
                Event(
                    kind="note",
                    actor=p.id,
                    rolls=[r],
                    text=f"{p.name()} fails to stand up and their activation ends.",
                )
            )
            return False, 0
        rec.emit(
            Event(
                kind="player_stood_up",
                actor=p.id,
                detail={"ma_used": p.movement()},
                rolls=[r],
                text=f"{p.name()} stands up, using their whole Move Allowance.",
            )
        )
        return True, p.movement()
    rec.emit(
        Event(
            kind="player_stood_up",
            actor=p.id,
            detail={"ma_used": p.ma_used + cost},
            text=f"{p.name()} stands up"
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
    rec = Recorder(match)
    unmodelled = unmodelled_skills(p)
    # The coach pre-commits, the same way they already do for `choice`,
    # `follow_up` and `push_to`. The engine cannot stop mid-resolution to ask, and
    # spending a Team Re-roll on somebody's behalf is not a decision it should be
    # making — `bb_game_legal` says how many are left before any of this.
    want_reroll = bool(cmd.get("team_reroll"))

    # 1. Stand up, if Prone. Must happen before anything else.
    if p.down == "prone":
        able, _spent = _stand_up(match, p, dice, rec)
        if not able:
            return Outcome(ok=False, events=rec.events, text=rec.events[-1].text, unmodelled=unmodelled)

    # Read the budget back off the player rather than adding the stand-up cost to
    # it. The event that stood them up has ALREADY been applied, so p.ma_used
    # includes it — adding `spent` again double-charged the 3 squares and
    # conjured a Rush roll out of a player who had movement to spare.
    used = p.ma_used
    needs_rush = (used + 1) > p.movement()
    needs_dodge = is_marked(match, p)

    # 2. Rush FIRST when a square needs both rolls — the S3 ordering.
    if needs_rush:
        rush = roll_modifier(match, p, "rush")
        r = roll_target(dice, "Rush", 2, rush.value, note=" ".join(rush.notes))
        if not r.passed and want_reroll and team_rerolls.spend(match, p, "Rush", dice, rec):
            r = roll_target(dice, "Rush (Team Re-roll)", 2, rush.value)
        if not r.passed:
            rec.emit(
                Event(
                    kind="player_moved",
                    actor=p.id,
                    detail={"x": x, "y": y, "ma_used": used + 1},
                    text=f"{p.name()} Rushes into ({x},{y})…",
                )
            )
            # Falling Over is a knockdown like any other: S3 says a player who
            # "is Knocked Down or Falls Over" becomes Prone AND risks injury. This
            # path used to just set them Prone, so tripping on a Rush was free.
            rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=f"…trips. {r.describe()}"))
            rec.absorb(knock_down(match, p, dice, cause="tripped Rushing"))
            return Outcome(
                ok=False,
                events=rec.events,
                turnover=True,
                text=f"{p.name()} failed the Rush and Falls Over in ({x},{y}) — turnover.",
                unmodelled=unmodelled,
            )
        rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=f"Rush succeeds. {r.describe()}"))

    # 3. Dodge, if leaving a Marked square.
    if needs_dodge:
        marking = dodge_modifier(match, p, x, y)
        # An opponent's Skill can modify our roll, so the hook is asked of the
        # players Marking the destination rather than of the one dodging.
        ctx = SkillContext(match=match, player=p, value=marking)
        for marker in markers_of_square(match, p.side, x, y):
            for skill, fn in hooks_for("opponent_dodge_modifier"):
                if marker.has_skill(skill):
                    fn(ctx)
        modifier = ctx.value
        # …and the dodging player's own Skills. `marking` rides along because
        # Stunty cancels exactly the Marking component and nothing else.
        mine = roll_modifier(match, p, "dodge", base=modifier, marking=marking, break_tackle_used=p.break_tackle_used)
        modifier = mine.value
        if mine.flags.get("break_tackle_spent"):
            rec.emit(
                Event(
                    kind="skill_spent",
                    actor=p.id,
                    detail={"flag": "break_tackle_used", "skill": "Break Tackle"},
                    text=f"{p.name()} uses Break Tackle — once per Turn.",
                )
            )
        note = " ".join(ctx.notes + mine.notes)

        r = roll_target(dice, "Dodge", agility_target(p), modifier, note=note)
        # Both attempts are kept. A log that shows only the successful re-roll
        # hides the failure that forced it, and the coach reads this log.
        dodge_rolls = [r]
        if not r.passed:
            # "When an opposition player attempts to Dodge away from a square in
            # this player's Tackle Zone, they cannot use the Dodge Skill." The
            # square being LEFT, so these are the markers of the origin — not the
            # ones that set the modifier above.
            tackled = SkillContext(match=match, player=p)
            for tackler in markers_of_square(match, p.side, p.x, p.y):
                for skill, fn in hooks_for("deny_dodge_skill"):
                    if tackler.has_skill(skill):
                        fn(tackled)
            # The Dodge Skill is once per TURN, not per activation, so the flag
            # lives on the player and is cleared when their turn starts.
            reroll = SkillContext(match=match, player=p, flags={"dodge_reroll_used": p.dodge_reroll_used})
            if not tackled.flags.get("denied"):
                for skill, fn in hooks_for("dodge_reroll"):
                    if p.has_skill(skill):
                        fn(reroll)
            elif p.has_skill("Dodge"):
                rec.emit(
                    Event(
                        kind="note",
                        actor=p.id,
                        text=f"{p.name()} is Tackled — the Dodge Skill does not apply.",
                    )
                )
            if reroll.flags.get("may_reroll"):
                rec.emit(
                    Event(
                        kind="skill_spent",
                        actor=p.id,
                        detail={"flag": "dodge_reroll_used", "skill": "Dodge"},
                        text=f"{p.name()} uses the Dodge Skill's re-roll — once per Turn.",
                    )
                )
                r = roll_target(dice, "Dodge (re-roll)", agility_target(p), modifier, note="Dodge skill")
                dodge_rolls.append(r)
            # A Skill re-roll is FREE, so it is always tried first and a Team
            # Re-roll only steps in when there was none — and never on a die that
            # is already a re-roll: "they may still never re-roll a re-roll".
            elif want_reroll and team_rerolls.spend(match, p, "Dodge", dice, rec):
                r = roll_target(dice, "Dodge (Team Re-roll)", agility_target(p), modifier)
                dodge_rolls.append(r)

        if not r.passed:
            # A failed Dodge still moves the player — they land in the square and
            # fall there, which matters for where the ball ends up.
            rec.emit(
                Event(
                    kind="player_moved",
                    actor=p.id,
                    detail={"x": x, "y": y, "ma_used": used + 1},
                    text=f"{p.name()} Dodges toward ({x},{y})…",
                )
            )
            rec.emit(Event(kind="note", actor=p.id, rolls=dodge_rolls, text=f"…and slips. {r.describe()}"))
            rec.absorb(knock_down(match, p, dice, cause="Falls Over"))
            return Outcome(
                ok=False,
                events=rec.events,
                turnover=True,
                text=f"{p.name()} failed the Dodge into ({x},{y}) and Falls Over — turnover.",
                unmodelled=unmodelled,
            )
        rec.emit(Event(kind="note", actor=p.id, rolls=dodge_rolls, text=f"Dodge succeeds. {r.describe()}"))

    rec.emit(
        Event(
            kind="player_moved",
            actor=p.id,
            detail={"x": x, "y": y, "ma_used": used + 1},
            text=f"{p.name()} moves to ({x},{y}).",
        )
    )

    # 4. The ball, if it is lying here. S3: the pick-up roll comes AFTER the rolls
    # that got you into the square (Rush, Dodge) and before anything else — which
    # is why this sits at the bottom rather than beside them.
    if match.ball.in_play and not match.ball.carrier and (match.ball.x, match.ball.y) == (x, y):
        events, turned_over = pick_up(match, p, dice, team_reroll=want_reroll)
        rec.absorb(events)
        if turned_over:
            return Outcome(
                ok=False,
                events=rec.events,
                turnover=True,
                text=f"{p.name()} failed to pick the ball up — turnover.",
                unmodelled=unmodelled,
            )

    # Scoring can happen on a plain Move, and is checked wherever position or
    # possession changes rather than only here.
    rec.absorb(check_touchdown(match, p))

    return Outcome(
        ok=True,
        events=rec.events,
        text=f"{p.name()} moves to ({x},{y}).",
        unmodelled=unmodelled,
    )


register("move", validate, resolve)
