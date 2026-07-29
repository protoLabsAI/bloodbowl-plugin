"""The Pass Action.

Named ``throw`` because ``pass`` is a Python keyword; the action registers as
"pass" and that is what a coach types.

Every rule below is quoted from the S3 source. The ONE thing that is not is how
far each range band reaches, because the rules define that by laying a physical
ruler on the table — see ruler.py, which isolates it and says so.

    PASSING ABILITY TEST, modifiers:
      Quick Pass no modifier · Short Pass -1 · Long Pass -2 · Long Bomb -3
      "Apply a -1 modifier for each opposition player Marking the player
       performing the Pass Action."

    ACCURATE   "If the Passing Ability Test is successful, or the roll is a
               natural 6 ... The ball will land in the target square."
    INACCURATE "the ball will Scatter (3) from the target square before landing."
    FUMBLED    "If the Passing Ability Test is a 1 after modifiers, or the roll is
               a natural 1 ... The ball is dropped and will Bounce from the
               throwing player's square and a Turnover will be caused."

    INTERCEPTIONS are checked "After determining which square the ball is destined
    to land in" — and the ruler is laid to THAT square, "which may not be the
    original target square!". Only Standing opposition players count, and
    "Players that have lost their Tackle Zone may not attempt to Intercept."
    Modifiers: -3 against an Accurate Pass, -2 against an Inaccurate one, and -1
    per opposition player Marking the interceptor. Success, or a natural 6,
    intercepts and causes a Turnover.
"""

from __future__ import annotations

import re

from ...pitch import in_bounds
from .. import rerolls as team_rerolls
from ..ball import bounce, catch, scatter, throw_in
from ..dice import roll_target
from ..events import Event
from ..ruler import band, in_corridor
from ..rules import markers_of_square
from ..skills import can_use, may_reroll, roll_modifier, unmodelled_skills
from ..state import Match
from ..weather import bands_allowed
from ..weather import name_of as weather_name
from . import Legality, Outcome, Recorder, ended, refuse_if_spent, register


def _passing_target(p) -> int:
    """PA as a number. A player with no PA cannot pass at all — a Troll's profile
    reads "-", and treating that as a 4+ would invent an ability."""
    m = re.search(r"\d+", str(p.player.PA or ""))
    return int(m.group(0)) if m else 0


def validate(match: Match, cmd: dict) -> Legality:
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    spent = refuse_if_spent(match, p, "pass")
    if spent:
        return Legality(False, spent)
    if p.place != "pitch" or p.down != "standing":
        return Legality(False, "only a Standing player on the pitch can Pass")
    if match.ball.carrier != p.id:
        return Legality(False, f"{p.name()} is not holding the ball")
    if not _passing_target(p):
        return Legality(False, f"{p.name()} has no Passing Ability and cannot Pass")

    try:
        x, y = int(cmd["x"]), int(cmd["y"])
    except (KeyError, TypeError, ValueError):
        return Legality(False, "a target square (x, y) is required")
    if not in_bounds(x, y):
        return Legality(False, f"({x},{y}) is off the pitch")
    if (x, y) == (p.x, p.y):
        return Legality(False, "you cannot pass to your own square")

    reach = band(p.x, p.y, x, y)
    if reach is None:
        return Legality(False, f"({x},{y}) is beyond a Long Bomb — out of range")
    name, modifier = reach
    # Blizzard: "when a player makes a Pass Action, they may only attempt to make
    # a Quick Pass or a Short Pass." A legality rather than a penalty, so it is
    # refused with a reason instead of thrown at long odds.
    allowed = bands_allowed(match.weather)
    if allowed is not None and name not in allowed:
        return Legality(False, f"a {name} cannot be thrown in a {weather_name(match.weather)}")
    marking = len(markers_of_square(match, p.side, p.x, p.y))
    return Legality(
        True,
        "",
        {
            "range": name,
            "range_modifier": modifier,
            "marking_passer": -marking,
            "target": _passing_target(p),
            "modifier": modifier - marking,
            "measured_ruler": True,
        },
    )


def _interceptors(match: Match, passer, lx: int, ly: int) -> list:
    """Standing opposition players the ruler overlaps on its way to the LANDING
    square. A Distracted player has lost its Tackle Zone and may not try."""
    out = []
    for q in match.on_pitch(match.opponent(passer.side)):
        if q.down != "standing" or getattr(q, "distracted", False):
            continue
        if in_corridor(passer.x, passer.y, lx, ly, q.x, q.y):
            out.append(q)
    return out


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    legal = validate(match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    from ..rules import agility_target

    p = match.by_id(str(cmd["player"]))
    x, y = int(cmd["x"]), int(cmd["y"])
    rec = Recorder(match)
    unmodelled = unmodelled_skills(p)
    d = legal.detail

    # Accurate (+1 on a Quick or Short Pass) and Nerves of Steel (ignore Marking)
    # both land here; the band and the Marking penalty ride along so each Skill can
    # read the one it cares about.
    ctx = roll_modifier(match, p, "pass", base=d["modifier"], marking=d["marking_passer"], range=d["range"])
    r = roll_target(dice, "Pass", d["target"], ctx.value, note=" ".join([d["range"], *ctx.notes]))
    if not r.passed and r.dice[0] != 1:
        # "This player may re-roll any failed Passing Ability Test when performing
        # a Pass Action." A natural 1 is a Fumble rather than a failed test, and is
        # resolved below rather than re-rolled.
        allowed, skill = may_reroll(match, p, "pass")
        if allowed:
            r = roll_target(dice, "Pass (re-roll)", d["target"], ctx.value, note=f"{skill} skill")
        elif cmd.get("team_reroll") and team_rerolls.spend(match, p, "Pass", dice, rec):
            r = roll_target(dice, "Pass (Team Re-roll)", d["target"], ctx.value)
    # "If the Passing Ability Test is a 1 after modifiers, or the roll is a natural
    # 1" — so a heavily modified pass can fumble on a die that was not a 1.
    fumbled = r.dice[0] == 1 or (r.total is not None and r.total <= 1)
    accurate = r.passed and not fumbled

    # SAFE PASS: "If this player rolls A NATURAL 1 when making a Passing Ability
    # Test, then it will not result in a Fumbled Pass. Instead, the player RETAINS
    # POSSESSION of the ball and their activation immediately ends. NO TURNOVER is
    # caused." Natural 1 only — a fumble that came from modifiers is still a
    # fumble, which is the distinction the rule is careful about.
    if fumbled and r.dice[0] == 1 and can_use(p, "Safe Pass"):
        rec.emit(
            Event(
                kind="pass_thrown",
                actor=p.id,
                detail={"outcome": "safe_pass", "range": d["range"], "x": x, "y": y},
                rolls=[r],
                text=f"{p.name()} nearly drops it, and hangs on. {r.describe()} "
                "Safe Pass: no Fumble, no Turnover, and the ball stays with them.",
            )
        )
        rec.emit(ended(p.id, "pass"))
        return Outcome(
            ok=True,
            events=rec.events,
            text=f"{p.name()} kept hold of the ball — Safe Pass, and their activation ends.",
            unmodelled=unmodelled,
        )

    if fumbled:
        rec.emit(
            Event(
                kind="pass_thrown",
                actor=p.id,
                detail={"outcome": "fumble", "range": d["range"], "x": x, "y": y},
                rolls=[r],
                text=f"{p.name()} fumbles the {d['range']} — the ball is dropped. {r.describe()}",
            )
        )
        rec.absorb(_drop_here(match, p, dice))
        rec.emit(ended(p.id, "pass"))
        return Outcome(
            ok=False,
            events=rec.events,
            turnover=True,
            text=f"{p.name()} fumbled the pass — turnover.",
            unmodelled=unmodelled,
        )

    rec.emit(
        Event(
            kind="pass_thrown",
            actor=p.id,
            detail={"outcome": "accurate" if accurate else "inaccurate", "range": d["range"], "x": x, "y": y},
            rolls=[r],
            text=f"{p.name()} throws an {'accurate' if accurate else 'inaccurate'} {d['range']} "
            f"to ({x},{y}). {r.describe()}",
        )
    )

    # The ball leaves the passer's hands either way.
    rec.emit(
        Event(
            kind="ball_moved",
            detail={"x": x, "y": y, "carrier": ""},
            text=f"The ball is in the air towards ({x},{y}).",
        )
    )

    if not accurate:
        # "the ball will Scatter (3) from the target square before landing"
        rec.absorb(scatter(match, dice, 3))

    lx, ly = match.ball.x, match.ball.y

    # Interceptions are checked against the square the ball is DESTINED to land in.
    for q in _interceptors(match, p, lx, ly):
        marking = -len(markers_of_square(match, q.side, q.x, q.y))
        ictx = roll_modifier(match, q, "intercept", base=(-3 if accurate else -2) + marking, marking=marking)
        ir = roll_target(dice, "Intercept", agility_target(q), ictx.value, note=" ".join(ictx.notes))
        if ir.passed:
            ev = Event(
                kind="ball_picked_up",
                actor=q.id,
                rolls=[ir],
                text=f"{q.name()} INTERCEPTS the pass! {ir.describe()}",
            )
            rec.emit(ev)
            rec.emit(ended(p.id, "pass"))
            return Outcome(
                ok=False,
                events=rec.events,
                turnover=True,
                text=f"{q.name()} intercepted the pass — turnover.",
                unmodelled=unmodelled,
            )
        rec.emit(Event(kind="note", actor=q.id, rolls=[ir], text=f"{q.name()} fails to intercept. {ir.describe()}"))

    # Landing: a player catches, an empty square bounces, off the pitch is a
    # throw-in — and after a Pass, a ball that ends loose or with the opposition
    # is a Turnover.
    if not in_bounds(lx, ly):
        rec.absorb(throw_in(match, dice, lx, ly))
    else:
        landed = match.at(lx, ly)
        if landed is None:
            rec.absorb(bounce(match, dice))
        else:
            rec.absorb(catch(match, landed, dice, team_reroll=bool(cmd.get("team_reroll"))))

    holder = match.by_id(match.ball.carrier) if match.ball.carrier else None
    ours = holder is not None and holder.side == p.side
    # GIVE AND GO: "If this player performs a Pass Action that is A QUICK PASS …
    # then, SO LONG AS A TURNOVER ISN'T CAUSED, their activation does not end once
    # the Pass is resolved. Instead, they may continue with their Move Action using
    # any movement they have remaining." Quick Pass only, and only if it worked.
    if ours and d["range"] == "Quick Pass" and can_use(p, "Give and Go"):
        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                detail={"skill": "Give and Go"},
                text=f"{p.name()} gives and goes — their activation continues.",
            )
        )
    else:
        rec.emit(ended(p.id, "pass"))
    return Outcome(
        ok=ours,
        events=rec.events,
        turnover=not ours,
        text=(
            f"{holder.name()} takes the pass."
            if ours
            else "The pass is not completed — the ball ends loose or with the opposition, so it is a turnover."
        ),
        unmodelled=unmodelled,
    )


def _drop_here(match: Match, p, dice) -> list[Event]:
    ev = Event(
        kind="ball_dropped",
        actor=p.id,
        detail={"x": p.x, "y": p.y},
        text=f"The ball bounces from {p.name()}'s square.",
    )
    match.apply(ev)
    return [ev, *bounce(match, dice)]


register("pass", validate, resolve)
