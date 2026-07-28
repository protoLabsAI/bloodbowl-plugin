"""The Blitz Action — a DECLARATION, not a manoeuvre.

Every rule below is quoted from the S3 source.

    "Simply put, a Blitz Action combines both a Move Action and a Block Action;
     however, only a single player may perform a Blitz Action each Turn. When a
     player declares a Blitz Action, they must also declare which opposition
     player is the intended target of the Block Action part of the Blitz Action.
     Players may not declare an opposition player as the intended target of the
     Block Action if they cannot reach the player at all with their Move Action
     (including any extra squares gained by attempting to Rush)."

    "When a player declares a Blitz Action, they may make a Move Action following
     all the normal rules for a Move Action. Additionally, if at any point during
     this Move Action they are adjacent to the opposition player they declared as
     their intended target, then they may perform a Block Action against them
     following all the normal rules for a Block Action, however, this Block
     Action will cost the player that declared the Blitz Action a point of Move
     Allowance. After the player has performed the Block Action, they can continue
     their Move Action using any remaining Move Allowance they have left."

    "Players are never required to perform the Block Action against their intended
     target if they decide not to, though they will still count as having used
     their team's one Blitz Action for the Turn."

WHY THIS IS A DECLARATION AND NOT ONE BIG COMMAND. "If at any point during this
Move Action" is a decision the coach makes step by step — walk two squares, see
whether the Dodge held, then decide whether to hit them or keep running. A single
`blitz(player, target, path)` call would have to choose for them. So this action
records the declaration and nothing else, movement stays `move` one square at a
time, and the Block stays `block`: the Blitz is the STATE that makes that Block
legal after the player has already moved, and that does not end the activation.

Which is also what the rules say it is — "any rules that interact with either of
these Actions will also interact with them when they are used as part of a Blitz
Action". A Blitz's Block is a Block Action. Reimplementing one here would be a
second Block that agrees with the first until it doesn't.

Rolls no dice, so it is safe to declare and then think.
"""

from __future__ import annotations

from ..events import Event
from ..rules import MAX_RUSHES, STAND_UP_COST, steps_to_mark
from ..skills import SkillContext, apply_value_hook, unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, register


def reach(match: Match, p, t) -> dict:
    """What this player could do about that one, in squares.

    ``steps`` to get adjacent, ``budget`` of Move Allowance including Rushes, and
    the two separate questions those answer: can they get there at all (which is
    what the rules gate the declaration on), and can they also afford the Block
    (which costs one more point on top).
    """
    stand_cost = 0
    if p.down == "prone":
        ctx = SkillContext(match=match, player=p, value=STAND_UP_COST)
        stand_cost = apply_value_hook("stand_up_cost", ctx, p)
    # The ceiling on ma_used is MA plus the two Rushes, so what is left is that
    # ceiling minus what has already gone.
    budget = p.movement() + MAX_RUSHES - p.ma_used - stand_cost
    steps = steps_to_mark(match, p, t)
    return {
        "steps": steps,
        "budget": budget,
        "stand_up_cost": stand_cost,
        "can_reach": steps is not None and steps <= budget,
        # "this Block Action will cost … a point of Move Allowance"
        "can_block": steps is not None and steps + 1 <= budget,
    }


def validate(match: Match, cmd: dict) -> Legality:
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
    if p.acted:
        return Legality(False, f"{p.name()} has already acted this turn")
    if match.blitz:
        who = match.by_id(str(match.blitz.get("player") or ""))
        return Legality(
            False,
            f"{match.clock.active} have already used their one Blitz this turn"
            + (f" — {who.name()} declared it" if who is not None else ""),
        )

    t = match.by_id(str(cmd.get("target") or ""))
    if t is None:
        return Legality(False, f"no target with id {cmd.get('target')!r}")
    if t.place != "pitch":
        return Legality(False, "the target is not on the pitch")
    if t.side == p.side:
        return Legality(False, "a Blitz targets an opposition player, not a team-mate")
    if t.down != "standing":
        # The Blitz text restricts only reachability, but the Block it exists to
        # deliver does not: "they must nominate one Standing opposition player
        # they are Marking to be the target of the Block Action." Declaring
        # against a Prone player would spend the team's one Blitz on a Block that
        # could never be legal, so it is refused rather than allowed and regretted.
        return Legality(False, f"{t.name()} is {t.down} — a Block may only target a Standing player")

    r = reach(match, p, t)
    if r["steps"] is None:
        return Legality(False, f"there is no route to {t.name()} — every square around them is taken")
    if not r["can_reach"]:
        return Legality(
            False,
            f"{t.name()} is {r['steps']} squares away and {p.name()} has {r['budget']} "
            f"(Move Allowance plus {MAX_RUSHES} Rushes) — out of reach",
        )
    return Legality(True, "", {"target": t.id, **r})


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    """Declare it. No dice — the movement and the Block roll their own."""
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    p = match.by_id(str(cmd["player"]))
    t = match.by_id(str(cmd["target"]))
    rec = Recorder(match)
    d = legal.detail

    warning = (
        ""
        if d["can_block"]
        else (
            f" NOTE: reaching {t.name()} takes {d['steps']} of {d['budget']} squares, "
            "leaving nothing for the Block itself, which costs one more."
        )
    )
    rec.emit(
        Event(
            kind="blitz_declared",
            actor=p.id,
            detail={"target": t.id, "steps": d["steps"], "budget": d["budget"], "can_block": d["can_block"]},
            text=f"{p.name()} declares a Blitz against {t.name()} "
            f"({d['steps']} squares away, {d['budget']} available)." + warning,
        )
    )
    return Outcome(
        ok=True,
        events=rec.events,
        text=f"{p.name()} is blitzing {t.name()}. Move as normal; the Block becomes legal once adjacent." + warning,
        unmodelled=sorted(set(unmodelled_skills(p)) | set(unmodelled_skills(t))),
    )


register("blitz", validate, resolve)
