"""The Block action: dice from Strength, a result, a push, and what it costs.

Modelled from the S3 text, quoted beside each branch. The parts most likely to be
got wrong from memory, and therefore the parts with a test each:

* THREE dice when one player has OVER DOUBLE the other's modified Strength — and
  "over double" is strictly greater, so ST 4 against ST 2 is two dice, not three.
* The STRONGER coach chooses. Blocking someone stronger than you hands THEM the
  pick of the dice, which is why it is usually a bad idea.
* Assists are "+1 for each player of the same team who is Marking the target and
  is NOT Marked by another opposing player" — the blocker themselves is not
  *another* opposing player, so they never cancel their own assists.
* STUMBLE becomes Push Back if the target has Dodge, and POW otherwise. It is not
  a re-roll and it costs the Dodge player nothing.
* PLAYER DOWN knocks down the BLOCKER — a Block can go wrong, and when it does
  during your own turn it is a turnover.
* The push arc is the direction plus its two neighbours (see rules.push_squares);
  the literal reading of the rulebook sentence over-generates on a diagonal.
"""

from __future__ import annotations

from ...pitch import in_bounds
from ..ball import check_touchdown
from ..dice import BLOCK_LABELS, roll_target
from ..events import Event
from ..injury import knock_down, risk_injury
from ..rules import (
    MAX_RUSHES,
    adjacent,
    assist_count,
    block_dice,
    has_tackle_zone,
    push_squares,
    strength_of,
)
from ..skills import SkillContext, hooks_for, unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, register


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.place != "pitch":
        return Legality(False, f"{p.name()} is not on the pitch")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    if p.down != "standing":
        return Legality(False, f"a {p.down} player cannot Block; they must stand up first")

    t = match.by_id(str(cmd.get("target") or ""))
    if t is None:
        return Legality(False, f"no target with id {cmd.get('target')!r}")
    if t.place != "pitch":
        return Legality(False, "the target is not on the pitch")
    if t.side == p.side:
        return Legality(False, "you cannot Block your own team-mate")
    if t.down != "standing":
        # S3: "they must nominate one Standing opposition player they are Marking
        # to be the target of the Block Action." Flooring someone who is already
        # down is what the Foul Action is for.
        return Legality(False, f"{t.name()} is {t.down} — a Block may only target a Standing player")

    blitzing = is_blitzing(match, p, t)
    if p.acted and not blitzing:
        return Legality(False, f"{p.name()} has already acted this turn")

    if not adjacent(p.x, p.y, t.x, t.y):
        return Legality(False, "a Block targets a player you are Marking — they must be adjacent")
    if not has_tackle_zone(p):
        return Legality(False, "a player with no Tackle Zone is not Marking anyone and cannot Block")

    # A Blitz's Block costs a point of Move Allowance, and a player who has none
    # left may Rush for it — but only within the two Rushes an activation allows.
    rush_for_block = 0
    if blitzing:
        over = (p.ma_used + 1) - p.movement()
        if over > MAX_RUSHES:
            return Legality(
                False,
                f"{p.name()} has no Move Allowance left for the Block "
                f"(a Blitz's Block costs one) and has used both Rushes",
            )
        rush_for_block = 1 if over > 0 else 0

    # Neither participant assists: the blocker is throwing the block and the
    # target cannot help themselves resist it.
    off = assist_count(match, p.side, t, exclude={p.id})
    dfn = assist_count(match, t.side, p, exclude={t.id})
    a_st, d_st = strength_of(match, p) + off, strength_of(match, t) + dfn
    n, chooser = block_dice(a_st, d_st)
    return Legality(
        True,
        "",
        {
            "dice": n,
            "chooser": chooser,
            "attacker_strength": a_st,
            "defender_strength": d_st,
            "offensive_assists": off,
            "defensive_assists": dfn,
            "blitz": blitzing,
            "costs_move_allowance": 1 if blitzing else 0,
            "rush_for_block": bool(rush_for_block),
        },
    )


def is_blitzing(match: Match, p, t) -> bool:
    """Is this Block the Block of a declared Blitz?

    All three have to line up: the right player, the declared target, and a Blitz
    whose Block has not been thrown yet. A Blitz does not license a second Block,
    nor a Block against somebody else — "they must ALSO declare which opposition
    player is the intended target".
    """
    b = match.blitz
    return bool(b) and b.get("player") == p.id and b.get("target") == t.id and not b.get("blocked")


def _push_to(match: Match, blocker, target, prefer: tuple | None = None):
    """Where a pushed player ends up, and whether that is off the pitch.

    Returns (square, kind) with kind in {"empty", "crowd", "occupied"}. A square
    off the pitch is the Crowd; an occupied one starts a Chain Push.
    """
    options = push_squares(blocker.x, blocker.y, target.x, target.y)
    on_pitch = [(x, y) for x, y in options if in_bounds(x, y)]
    empty = [sq for sq in on_pitch if match.at(*sq) is None]
    if prefer and tuple(prefer) in empty:
        return tuple(prefer), "empty"
    if empty:
        return empty[0], "empty"
    # S3: pushed into the Crowd when adjacent to a Sideline (or in an End Zone)
    # with no unoccupied square to take.
    off = [sq for sq in options if not in_bounds(*sq)]
    if off:
        return off[0], "crowd"
    return on_pitch[0], "occupied"


def _do_push(match: Match, blocker, target, dice, rec: Recorder, pushed: list, prefer=None, depth: int = 0) -> None:
    """Push one player, following the chain if the square is taken.

    Everyone shoved is collected in ``pushed`` rather than scored here. S3 lets a
    push into the End Zone score, but ALSO says a player "Knocked Down as they
    move into the opposition End Zone" does not — and a POW pushes first and knocks
    down second. Scoring at the moment of the push gets the POW case wrong, so the
    check waits until the result has fully resolved and then simply asks who is
    Standing in an End Zone holding the ball.
    """
    if depth > 12:  # a ring of players cannot push forever
        return

    square, kind = _push_to(match, blocker, target, prefer)

    if kind == "crowd":
        rec.emit(
            Event(
                kind="player_left_pitch",
                actor=target.id,
                detail={"reason": "crowd"},
                text=f"{target.name()} is Pushed into the Crowd.",
            )
        )
        rec.absorb(risk_injury(match, target, dice, by=blocker))
        return

    if kind == "occupied":
        # S3 Chain Push: the occupant is pushed "as if they had been Pushed Back by
        # the player who is now occupying their square". Prone and Stunned players
        # can be chain pushed too.
        occupant = match.at(*square)
        _do_push(match, target, occupant, dice, rec, pushed, depth=depth + 1)

    rec.emit(
        Event(
            kind="player_pushed",
            actor=target.id,
            detail={"x": square[0], "y": square[1], "by": blocker.id},
            text=f"{target.name()} is Pushed Back to ({square[0]},{square[1]}).",
        )
    )
    pushed.append(target)


def _uses_block(match: Match, p) -> tuple[bool, list[str]]:
    ctx = SkillContext(match=match, player=p)
    for skill, fn in hooks_for("both_down_optout"):
        if p.has_skill(skill):
            fn(ctx)
    return bool(ctx.flags.get("stays_up")), ctx.notes


def _choose(faces: list[str], chooser: str, acting: str, choice) -> int:
    """Which die is applied.

    The stronger coach chooses. When that is the ACTING coach we honour their
    pick; when it is the defending side there is no second coach at the table, so
    the engine picks the worst result for the attacker — playing the opposition as
    well as it can rather than conveniently badly.
    """
    if chooser == "attacker" and acting == "attacker":
        try:
            i = int(choice)
        except (TypeError, ValueError):
            i = 0
        return max(0, min(i, len(faces) - 1))
    order = ["player_down", "both_down", "push_back", "stumble", "pow"]
    return min(range(len(faces)), key=lambda i: order.index(faces[i]))


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    from ..dice import Roll

    p = match.by_id(str(cmd["player"]))
    t = match.by_id(str(cmd["target"]))
    rec = Recorder(match)
    unmodelled = sorted(set(unmodelled_skills(p)) | set(unmodelled_skills(t)))
    n, chooser = legal.detail["dice"], legal.detail["chooser"]
    blitzing = legal.detail["blitz"]

    # A Blitz's Block is paid for BEFORE it is thrown: "this Block Action will
    # cost the player that declared the Blitz Action a point of Move Allowance",
    # and "if a player has used all of their Move Allowance before making the
    # Block Action against their intended target then they may attempt to Rush as
    # normal to gain the extra point of Move Allowance required".
    if blitzing:
        if legal.detail["rush_for_block"]:
            r = roll_target(dice, "Rush", 2, note="for the Blitz's Block")
            if not r.passed:
                # "On a 1, the player trips and Falls Over in the square they were
                # attempting to Rush into and their activation immediately ends."
                # There is no square here — the Rush buys the Block, not a step.
                # The rules address the same shape for a Jump: "If a Rush attempt
                # to Jump over a player fails, the rushing player will Fall Over in
                # the square they are in rather than the square they are attempting
                # to Jump into." So: where they stand. INFERRED from that
                # precedent, which is the nearest case the text actually settles.
                rec.emit(
                    Event(
                        kind="note",
                        actor=p.id,
                        rolls=[r],
                        text=f"{p.name()} Rushes for the Blitz and trips before landing the Block. {r.describe()}",
                    )
                )
                rec.absorb(knock_down(match, p, dice, cause="tripped Rushing"))
                return Outcome(
                    ok=False,
                    events=rec.events,
                    turnover=True,
                    text=f"{p.name()} failed the Rush and Falls Over before the Block — turnover.",
                    unmodelled=unmodelled,
                )
            rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=f"Rush succeeds. {r.describe()}"))
        rec.emit(
            Event(
                kind="move_allowance_spent",
                actor=p.id,
                detail={"ma_used": p.ma_used + 1, "reason": "blitz_block"},
                text=f"The Blitz's Block costs {p.name()} a square of movement.",
            )
        )

    faces = dice.block(n)
    face = faces[_choose(faces, chooser, "attacker", cmd.get("choice"))]
    roll = Roll(kind="Block", dice=list(faces), note=f"{n} dice, {chooser} chooses")
    dice.rolls.append(roll)

    first = rec.emit(
        Event(
            kind="block_rolled",
            actor=p.id,
            detail={"faces": list(faces), "chosen": face, "chooser": chooser, "target": t.id, "blitz": blitzing},
            rolls=[roll],
            text=f"{p.name()} {'Blitzes' if blitzing else 'Blocks'} {t.name()}: "
            + ", ".join(BLOCK_LABELS[f] for f in faces)
            + f" — {BLOCK_LABELS[face]} applied ({chooser} chooses).",
        )
    )

    turnover = False
    pushed: list = []
    follow_up = bool(cmd.get("follow_up", True))
    dodged = face == "stumble" and t.has_skill("Dodge")

    def _push_and_follow(knock_after: bool) -> None:
        vacated = (t.x, t.y)
        _do_push(match, p, t, dice, rec, pushed, prefer=cmd.get("push_to"))
        if knock_after and t.place == "pitch":
            # "Apply the Push Back result. The target is then Knocked Down in the
            # square they are now in" — so the Armour Roll happens where they land.
            rec.absorb(knock_down(match, t, dice, by=p))
        if follow_up and match.at(*vacated) is None:
            rec.emit(
                Event(
                    kind="player_followed_up",
                    actor=p.id,
                    detail={"x": vacated[0], "y": vacated[1]},
                    text=f"{p.name()} follows up to ({vacated[0]},{vacated[1]}).",
                )
            )

    if dodged:
        rec.emit(
            Event(
                kind="note",
                actor=t.id,
                text=f"{t.name()} has Dodge, so Stumble becomes Push Back.",
            )
        )

    if face == "push_back" or dodged:
        _push_and_follow(knock_after=False)

    elif face in ("pow", "stumble"):
        # Stumble without Dodge IS POW, per the die's own text.
        _push_and_follow(knock_after=True)

    elif face == "player_down":
        rec.absorb(knock_down(match, p, dice, by=t))
        turnover = True

    elif face == "both_down":
        going_down = []
        for who, other in ((p, t), (t, p)):
            stays, notes = _uses_block(match, who)
            if stays:
                rec.emit(Event(kind="note", actor=who.id, text=f"{who.name()} — {'; '.join(notes)}."))
                continue
            going_down.append((who, other))
        for who, other in going_down:
            rec.absorb(knock_down(match, who, dice, by=other))
        # A turnover only when it is the ACTIVE team's player who went down.
        turnover = any(who.id == p.id for who, _ in going_down)

    # Now that everything has resolved, see if anyone ended up Standing in an End
    # Zone holding the ball — a shove can score, a POW cannot.
    for who in pushed:
        rec.absorb(check_touchdown(match, who))

    # "After the player has performed the Block Action, they can continue their
    # Move Action using any remaining Move Allowance they have left." So a Blitz's
    # Block does NOT end the activation — the one Block that doesn't.
    #
    # `acted` is still set, because it is what stops a SECOND Block Action, and
    # movement is governed by Move Allowance rather than by this flag. The Blitz's
    # own Block is already spent (`match.blitz["blocked"]`), so the exemption in
    # validate does not fire twice.
    left = max(0, p.movement() - p.ma_used)
    if blitzing and not turnover and p.down == "standing" and p.place == "pitch":
        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                detail={"blitz": True, "movement_left": left},
                text=f"{p.name()} may keep moving — {left} square(s) of Move Allowance left."
                if left
                else f"{p.name()} has no Move Allowance left, but may still Rush.",
            )
        )
    else:
        rec.emit(ended(p.id, "blitz" if blitzing else "block", f"{p.name()}'s Block Action is over."))

    return Outcome(
        ok=not turnover,
        events=rec.events,
        turnover=turnover,
        text=first.text,
        unmodelled=unmodelled,
    )


register("block", validate, resolve)
