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
from .. import leaving
from .. import rerolls as team_rerolls
from ..ball import check_touchdown, pick_up
from ..dice import roll_target
from ..events import Event
from ..injury import knock_down, place_prone
from ..rules import (
    MAX_RUSHES,
    STAND_UP_COST,
    STAND_UP_ROLL,
    adjacent,
    agility_target,
    dodge_modifier,
    is_chomped,
    is_marked,
    jump_over,
    markers_of_square,
)
from ..skills import SkillContext, apply_value_hook, can_use, hooks_for, roll_modifier, unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, register


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
    if is_chomped(match, p):
        chomper = match.by_id(p.chomped_by)
        return Legality(False, f"{p.name()} is Chomped by {chomper.name()} and cannot leave that square")
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
    if match.at(x, y) is not None:
        return Legality(False, f"({x},{y}) is occupied")
    over = None if adjacent(p.x, p.y, x, y) else jump_over(match, p, x, y)
    if not adjacent(p.x, p.y, x, y) and over is None:
        return Legality(
            False,
            "a Move goes one square at a time — unless it is a Jump over a Prone or "
            "Stunned player, into a square on the far side of them",
        )

    # A Jump moves TWO squares and costs two: "As the player is moving 2 squares
    # when jumping, it will also cost 2 squares of Move Allowance."
    step_cost = 2 if over is not None else 1
    # Cost of the step, including standing up first if Prone.
    stand_cost = 0
    if p.down == "prone":
        ctx = SkillContext(match=match, player=p, value=STAND_UP_COST)
        stand_cost = apply_value_hook("stand_up_cost", ctx, p)
    budget = p.movement()
    used = p.ma_used + stand_cost
    rushes_needed = max(0, (used + step_cost) - budget)
    # SPRINT: "they may attempt to Rush ONE ADDITIONAL TIME than they would
    # normally be allowed to." A third attempt, not a free square — it is still a
    # 2+ each time, so it is a third chance to trip as much as a third square.
    allowed = apply_value_hook("extra_rush", SkillContext(match=match, player=p, value=MAX_RUSHES), p)
    if rushes_needed > allowed:
        return Legality(
            False,
            f"out of movement: {p.name()} has MA {budget}, has used {p.ma_used}"
            + (f" and needs {stand_cost} to stand up" if stand_cost else "")
            + f", and may Rush at most {allowed} times",
        )

    detail = {
        "rush": rushes_needed > 0,
        "rushes": rushes_needed,
        "stand_up_cost": stand_cost,
        "dodge": is_marked(match, p) and over is None,
        "dodge_modifier": dodge_modifier(match, p, x, y),
        "markers_of_target": [m.id for m in markers_of_square(match, p.side, x, y)],
    }
    if over is not None:
        # A Jump is its own Agility Test and is NOT a Dodge — the modifier is the
        # WORSE of the two squares rather than the destination's.
        here = len(markers_of_square(match, p.side, p.x, p.y))
        there = len(markers_of_square(match, p.side, x, y))
        detail.update(
            jump=True,
            jump_over=over.id,
            cost=2,
            jump_modifier=-max(here, there),
            dodge=False,
        )
    return Legality(True, "", detail)


def _stand_up(match: Match, p, dice, rec: Recorder) -> tuple[bool, int]:
    """Returns (still able to act, movement spent). A low-MA player may fail and
    lose the activation entirely."""
    ctx = SkillContext(match=match, player=p, value=STAND_UP_COST)
    cost = apply_value_hook("stand_up_cost", ctx, p)
    if p.movement() <= 2 and cost > 0:
        # Through the hook: Timmm-ber! adds +1 for each Open Standing team-mate
        # adjacent, which is a modifier on this test like any other.
        up = roll_modifier(match, p, "stand_up")
        r = roll_target(dice, "stand up", STAND_UP_ROLL, up.value, note="MA 2 or less " + " ".join(up.notes))
        # "A roll of A NATURAL 1 WILL STILL FAIL AS NORMAL" — however many friends
        # are hauling, so the modifier cannot rescue the worst roll.
        if r.dice[0] == 1:
            r.passed = False
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
    over = legal.detail.get("jump_over")
    step_cost = 2 if over else 1
    needs_rush = (used + step_cost) > p.movement()
    needs_dodge = is_marked(match, p) and not over

    # 2. Rush FIRST when a square needs both rolls — the S3 ordering.
    if needs_rush:
        rush = roll_modifier(match, p, "rush")
        r = roll_target(dice, "Rush", 2, rush.value, note=" ".join(rush.notes))
        # A Skill re-roll is FREE, so it goes first and a Team Re-roll only steps
        # in when there was none — and never on a die that is already a re-roll.
        feet = SkillContext(match=match, player=p, flags={"rush_reroll_used": p.rush_reroll_used})
        if not r.passed:
            for skill, fn in hooks_for("rush_reroll"):
                if can_use(p, skill):
                    fn(feet)
        if not r.passed and feet.flags.get("may_reroll"):
            rec.emit(
                Event(
                    kind="skill_spent",
                    actor=p.id,
                    detail={"flag": "rush_reroll_used", "skill": "Sure Feet"},
                    text=f"{p.name()} uses Sure Feet's re-roll — once per Turn.",
                )
            )
            r = roll_target(dice, "Rush (Sure Feet)", 2, rush.value)
        elif not r.passed and want_reroll and team_rerolls.spend(match, p, "Rush", dice, rec):
            r = roll_target(dice, "Rush (Team Re-roll)", 2, rush.value)
        if not r.passed:
            rec.emit(
                Event(
                    kind="player_moved",
                    actor=p.id,
                    detail={"x": x, "y": y, "ma_used": used + step_cost},
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

    # 2b. The Jump's own Agility Test, AFTER any Rush — "they will need to roll for
    # each Rush attempt BEFORE rolling to Jump".
    if over:
        jumped = match.by_id(over)
        # Through the hook rather than the bare number: a Jump is an Agility Test
        # like any other, and Very Long Legs has a +1 for it.
        jctx = roll_modifier(match, p, "jump", base=legal.detail["jump_modifier"])
        mod = jctx.value
        jr = roll_target(
            dice,
            "Jump",
            agility_target(p),
            mod,
            note=f"over {jumped.name()}" + "".join(f" · {n}" for n in jctx.notes),
        )
        if not jr.passed and want_reroll and team_rerolls.spend(match, p, "Jump", dice, rec):
            jr = roll_target(dice, "Jump (Team Re-roll)", agility_target(p), mod)
        if not jr.passed:
            # "If a natural 1 is rolled for the Agility Test, the player will
            # instead Fall Over IN THE SQUARE THEY ARE IN"; any other failure puts
            # them in the target square first. Two different squares, and the
            # difference decides where a dropped ball lands.
            if jr.dice[-1] != 1:
                rec.emit(
                    Event(
                        kind="player_moved",
                        actor=p.id,
                        detail={"x": x, "y": y, "ma_used": used + step_cost},
                        text=f"{p.name()} Jumps over {jumped.name()} toward ({x},{y})…",
                    )
                )
            rec.emit(Event(kind="note", actor=p.id, rolls=[jr], text=f"…and lands badly. {jr.describe()}"))
            rec.absorb(knock_down(match, p, dice, cause="Falls Over"))
            return Outcome(
                ok=False,
                events=rec.events,
                turnover=True,
                text=f"{p.name()} failed the Jump over {jumped.name()} — turnover.",
                unmodelled=unmodelled,
            )
        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                rolls=[jr],
                text=f"{p.name()} Jumps over {jumped.name()}. {jr.describe()}",
            )
        )

    # 3. The Skills that fire when an opponent leaves your Tackle Zone. TENTACLES
    # comes FIRST because it stops them leaving at all — a Dodge that never
    # happens cannot be failed, re-rolled or Diving-Tackled. It applies to a Jump
    # as well as a Dodge: "attempts to Dodge, Jump or Leap away".
    leaving_markers = markers_of_square(match, p.side, p.x, p.y)
    if leaving_markers and leaving.tentacles(match, p, leaving_markers, dice, rec):
        rec.emit(ended(p.id, "move", f"{p.name()} is held fast — their activation ends."))
        return Outcome(
            ok=False,
            events=rec.events,
            text=f"{p.name()} cannot break free of the Tentacles — their activation ends.",
            unmodelled=unmodelled,
        )

    # 4. Dodge, if leaving a Marked square.
    vacated = (p.x, p.y)
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

        # DIVING TACKLE, after the roll and after every re-roll: "an Agility test
        # has been rolled and any modifiers and re-rolls have been applied". The
        # -2 costs the tackler their feet whether or not it lands, so the engine
        # spends it only when it turns a success into a failure — any other use is
        # pure loss and nobody at the table would want one.
        diver = None
        if r.passed:
            lowered, tackler = leaving.diving_tackle(match, p, leaving_markers, dice, rec, modifier)
            if tackler is not None and (r.total or 0) + (lowered - modifier) < (r.target or 0):
                r.modifier = lowered
                r.total = (r.total or 0) + (lowered - modifier)
                r.passed = False
                r.note = (r.note + " · Diving Tackle -2").strip(" ·")
                diver = tackler
                rec.emit(
                    Event(
                        kind="note",
                        actor=tackler.id,
                        detail={"skill": "Diving Tackle", "target": p.id},
                        text=f"{tackler.name()} throws themselves at {p.name()}'s legs — -2, and the Dodge fails.",
                    )
                )

        if not r.passed:
            # A failed Dodge still moves the player — they land in the square and
            # fall there, which matters for where the ball ends up.
            rec.emit(
                Event(
                    kind="player_moved",
                    actor=p.id,
                    detail={"x": x, "y": y, "ma_used": used + step_cost},
                    text=f"{p.name()} Dodges toward ({x},{y})…",
                )
            )
            rec.emit(Event(kind="note", actor=p.id, rolls=dodge_rolls, text=f"…and slips. {r.describe()}"))
            # ARM BAR: "+1 to either the Armour Roll or Injury Roll" for an
            # opponent who Fell Over leaving. Spent the same way Mighty Blow's is.
            bar = leaving.arm_bar(match, p, leaving_markers, rec)
            rec.absorb(knock_down(match, p, dice, cause="Falls Over", bonus=bar))
            # The Diving Tackler lands LAST, in the square the dodger vacated —
            # after the dodger has left it. Placing them there first would put two
            # players on one square for the length of a knock-down, and `match.at`
            # would answer with whichever it found.
            if diver is not None:
                rec.absorb(place_prone(match, diver, dice, reason="Diving Tackle"))
                rec.emit(
                    Event(
                        kind="player_pushed",
                        actor=diver.id,
                        detail={"x": vacated[0], "y": vacated[1], "diving_tackle": True},
                        text=f"{diver.name()} lands Prone in ({vacated[0]},{vacated[1]}).",
                    )
                )
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
            detail={"x": x, "y": y, "ma_used": used + step_cost},
            text=f"{p.name()} " + (f"lands at ({x},{y})." if over else f"moves to ({x},{y})."),
        )
    )

    # FUMBLEROOSKI: "When this player performs a Move Action WHILST THEY ARE IN
    # POSSESSION OF THE BALL, they may choose to PLACE the ball on the ground in
    # ANY SQUARE THEY MOVE OUT OF during their Move Action. THIS WILL NOT CAUSE A
    # TURNOVER." Placed, not dropped — no bounce — and it is a CHOICE, so it is a
    # command flag rather than something the engine does on anybody's behalf.
    if cmd.get("drop_ball") and match.ball.carrier == p.id and can_use(p, "Fumblerooski"):
        rec.emit(
            Event(
                kind="ball_moved",
                detail={"x": vacated[0], "y": vacated[1], "carrier": "", "fumblerooski": True},
                text=f"Fumblerooski: {p.name()} leaves the ball in ({vacated[0]},{vacated[1]}). No Turnover.",
            )
        )

    # SHADOWING, once they are actually gone: "this player is immediately placed
    # into the square that the opposition player vacated".
    if needs_dodge and leaving_markers:
        leaving.shadowing(match, p, leaving_markers, dice, rec, vacated)

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
