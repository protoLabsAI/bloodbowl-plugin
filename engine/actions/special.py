"""Special Actions — the ones a Skill or Trait grants.

    "There are many Skills and Traits that will provide a player with the option
     to declare a Special Action … Each Special Action will allow the player to
     perform a more unique action on the pitch, such as using a contraption of
     some sort, transfixing an opposition player where they stand, or even using a
     hidden blade to stab out at an opponent!"

    REPLACING ACTIONS: "Some Special Actions allow a player to replace the Block
     Action made as part of a Blitz Action with the Special Action. When this
     happens, this will still count as a team's one Blitz Action of the Turn.
     Additionally, it is worth noting that even though the Special Action is
     replacing a Block Action, IT IS NOT ONE ITSELF and so any rules, Skills or
     Traits that affect a Block Action will have no effect."

Five of them share a shape closely enough to be one table: declared on
activation, aimed at an adjacent or Marked opposition player, one D6, and the
activation ends. They differ in the target rule, the roll and the consequence, so
those are the three columns.

    STAB              "select a Standing opposition player adjacent to this player
                      and make an Armour Roll for the selected player. THIS ARMOUR
                      ROLL CANNOT BE MODIFIED IN ANY WAY."
    PROJECTILE VOMIT  "roll a D6. On a 2+ … Make an Armour Roll for the targeted
                      player … On a 1, this player covers THEMSELVES in acidic
                      bile; make an Armour Roll for THIS player."
    BREATHE FIRE      "roll a D6, applying a -1 modifier if the target has an ST of
                      5 or higher. On a 1, this player is immediately Knocked Down.
                      On a 2-3, nothing happens. On a 4+, the opposition player is
                      immediately Placed Prone. If the roll is a NATURAL 6, the
                      opposition player is Knocked Down instead."
    CHAINSAW          "roll a D6. On a 2+, this player immediately makes an Armour
                      Roll against one opposition player they are Marking, applying
                      a +3 modifier … On a 1, the Chainsaw will Kick-back and this
                      player is Knocked Down instead."
    CHOMP             "roll a D6. On a 1-2 nothing happens. On a 3+, the opposition
                      player is considered to be Chomped. Whilst Chomped, the
                      opposition player CANNOT LEAVE THE SQUARE they are in whilst
                      this player remains Marking them."

"There is no limit to the number of players that can declare this Special Action
each Turn" — for all five. Kick Team-mate is the exception and says so, so it is
not in this table.
"""

from __future__ import annotations

from ..events import Event
from ..injury import injury_roll, knock_down, place_prone, risk_injury
from ..rules import adjacent, has_tackle_zone, strength_of
from ..skills import unmodelled_skills
from ..state import Match
from . import Legality, Outcome, Recorder, ended, register

# skill -> (action name, needs the target to be MARKED rather than merely adjacent)
SPECIALS = {
    "Stab": ("stab", False),
    "Ball & Chain": ("ball_chain", None),
    "Bombardier": ("throw_bomb", None),
    "Projectile Vomit": ("vomit", False),
    "Breathe Fire": ("breathe_fire", True),
    "Chainsaw": ("chainsaw", True),
    "Monstrous Mouth": ("chomp", True),
    "Hypnotic Gaze": ("gaze", True),
}
BY_ACTION = {name: (skill, marked) for skill, (name, marked) in SPECIALS.items()}


def _validate(action: str, match: Match, cmd: dict) -> Legality:
    skill, needs_marked = BY_ACTION[action]
    if match.over:
        return Legality(False, "the match is over")
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None:
        return Legality(False, f"no player with id {cmd.get('player')!r}")
    if p.side != match.clock.active:
        return Legality(False, f"it is {match.clock.active}'s turn, and that player is {p.side}")
    if p.done:
        return Legality(False, f"{p.name()}'s activation is over")
    # A Special Action may REPLACE the Block of a Blitz, and that is the one way
    # it may follow a Move — otherwise it is the whole activation.
    blitzing = bool(match.blitz) and match.blitz.get("player") == p.id and not match.blitz.get("blocked")
    if p.acted and not blitzing:
        return Legality(False, f"{p.name()} has already acted this turn")
    if p.place != "pitch" or p.down != "standing":
        return Legality(False, f"only a Standing player on the pitch can use {skill}")
    if not p.has_skill(skill):
        return Legality(False, f"{p.name()} does not have {skill}")

    if needs_marked is None:
        # Ball & Chain aims at a compass point and Throw Bomb at a square, so
        # neither names a player at all.
        return Legality(True, "", {"skill": skill, "target": "", "replaces_blitz_block": blitzing})

    t = match.by_id(str(cmd.get("target") or ""))
    if t is None:
        return Legality(False, f"no target with id {cmd.get('target')!r}")
    if t.side == p.side or t.place != "pitch":
        return Legality(False, "a Special Action targets an opposition player on the pitch")
    if t.down != "standing":
        return Legality(False, f"{t.name()} is {t.down} — these target a Standing player")
    if not adjacent(p.x, p.y, t.x, t.y):
        return Legality(False, f"{t.name()} is not adjacent")
    if needs_marked and not has_tackle_zone(p):
        return Legality(False, "a player with no Tackle Zone is not Marking anyone")
    return Legality(True, "", {"skill": skill, "target": t.id, "replaces_blitz_block": blitzing})


def _unmodified_armour(match: Match, victim, dice, rec: Recorder, why: str, modifier: int = 0) -> None:
    """An Armour Roll that is NOT a knock-down: nobody falls over, and the roll is
    made straight for the victim.

    Stab and Projectile Vomit both say "This Armour Roll cannot be modified in any
    way", which is why they pass no `by` — Mighty Blow and Claws both hang off the
    player responsible, and letting them through here would modify the unmodifiable.
    """
    rec.absorb(risk_injury(match, victim, dice, by=None, armour_modifier=modifier))


def _resolve(action: str, match: Match, cmd: dict, dice) -> Outcome:
    legal = _validate(action, match, cmd)
    if not legal.ok:
        return Outcome(ok=False, text=legal.reason)

    from ..dice import Roll, roll_target

    p = match.by_id(str(cmd["player"]))
    t = match.by_id(str(legal.detail["target"])) if legal.detail["target"] else None
    rec = Recorder(match)
    skill = legal.detail["skill"]
    unmodelled = sorted(set(unmodelled_skills(p)) | (set(unmodelled_skills(t)) if t else set()))
    turnover = False

    rec.emit(
        Event(
            kind="special_action",
            actor=p.id,
            detail={
                "skill": skill,
                "target": t.id if t else "",
                "replaces_blitz_block": legal.detail["replaces_blitz_block"],
            },
            text=f"{p.name()} uses {skill}"
            + (f" on {t.name()}." if t else ".")
            + (" (replacing the Blitz's Block)" if legal.detail["replaces_blitz_block"] else ""),
        )
    )

    if action == "stab":
        _unmodified_armour(match, t, dice, rec, "Stab")

    elif action == "vomit":
        r = roll_target(dice, "Projectile Vomit", 2)
        rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=r.describe()))
        # "On a 1, this player covers THEMSELVES in acidic bile" — the roll is made
        # for whoever it landed on, and that can be the player who made it.
        _unmodified_armour(match, t if r.passed else p, dice, rec, "Projectile Vomit")
        turnover = not r.passed and p.down != "standing"

    elif action == "gaze":
        # HYPNOTIC GAZE: "they select a Standing opposition player adjacent to them
        # and roll a D6. On a 1-2, nothing happens and this player's activation
        # immediately ends. On a 3+, the selected opposition player becomes
        # DISTRACTED and this player's activation immediately ends."
        #
        # Either way the activation ends, which is what makes it a real cost: it
        # is not a free debuff, it is a whole player's turn spent on one.
        r = roll_target(dice, "Hypnotic Gaze", 3)
        rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=r.describe()))
        if r.passed:
            rec.emit(
                Event(
                    kind="player_status",
                    actor=t.id,
                    detail={"distracted": True, "skill": "Hypnotic Gaze"},
                    text=f"{t.name()} is transfixed — Distracted until they are next activated.",
                )
            )

    elif action == "breathe_fire":
        mod = -1 if strength_of(match, t) >= 5 else 0
        d = dice.d6()
        roll = Roll(kind="Breathe Fire", dice=[d], total=d + mod, modifier=mod, note=f"vs ST {strength_of(match, t)}")
        dice.rolls.append(roll)
        total = d + mod
        rec.emit(Event(kind="note", actor=p.id, rolls=[roll], text=roll.describe()))
        if total <= 1:
            rec.absorb(knock_down(match, p, dice, cause="scorched by their own fire"))
            turnover = True
        elif d == 6:
            # "If the roll is a NATURAL 6, the opposition player is Knocked Down
            # instead" — natural, so a -1 for a big target cannot take it away.
            rec.absorb(knock_down(match, t, dice, by=p, cause="set alight"))
        elif total >= 4:
            rec.absorb(place_prone(match, t, dice, reason="Breathe Fire"))

    elif action == "chainsaw":
        r = roll_target(dice, "Chainsaw", 2)
        rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=r.describe()))
        if r.passed:
            # "+3 modifier to the Armour Roll" — and a chainsaw is not a Block, so
            # nothing else modifies it either.
            _unmodified_armour(match, t, dice, rec, "Chainsaw", modifier=3)
        else:
            rec.emit(Event(kind="note", actor=p.id, text="The Chainsaw kicks back!"))
            rec.absorb(knock_down(match, p, dice, cause="cut by their own Chainsaw"))
            turnover = True

    elif action == "chomp":
        r = roll_target(dice, "Chomp", 3)
        rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=r.describe()))
        if r.passed:
            rec.emit(
                Event(
                    kind="player_status",
                    actor=t.id,
                    detail={"chomped": p.id},
                    text=f"{t.name()} is Chomped — they cannot leave that square while {p.name()} Marks them.",
                )
            )

    if action == "ball_chain":
        turnover = _ball_and_chain(match, cmd, dice, rec)
    elif action == "throw_bomb":
        turnover = _throw_bomb(match, cmd, dice, rec)

    rec.emit(ended(p.id, action, f"{p.name()}'s {skill} ends their activation."))
    return Outcome(
        ok=not turnover,
        events=rec.events,
        turnover=turnover,
        text=f"{p.name()} used {skill}" + (f" on {t.name()}." if t else "."),
        unmodelled=unmodelled,
    )


# --- the two that do not fit the table ------------------------------------


def _ball_and_chain(match: Match, cmd: dict, dice, rec: Recorder):
    """BALL & CHAIN: "the ONLY action they can declare is a Ball & Chain Special
    Action … position the Throw-in Template over this player so it faces one of
    the two End Zones or either Sideline. Then roll a D6 and move this player into
    the square as indicated … A player that moves in this manner DOES NOT HAVE TO
    MAKE AN AGILITY TEST TO DODGE away from another player's Tackle Zone; they
    will automatically pass … A player performing a Ball & Chain Action can move a
    number of squares UP TO THEIR MA."

    Movement without agency: the coach picks the facing, the die picks the square,
    and a wall or another player simply stops them. Dodges are free, which is what
    makes the Trait survivable at all.
    """
    from ...pitch import in_bounds
    from ..ball import THROW_IN_ARROWS, pick_up
    from ..dice import Roll

    p = match.by_id(str(cmd["player"]))
    facing = str(cmd.get("facing") or "north")
    ring = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
    base = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}.get(facing, (0, -1))
    for _ in range(p.movement()):
        d = dice.d6()
        roll = Roll(kind="Ball & Chain", dice=[d], total=d, note=f"facing {facing}")
        dice.rolls.append(roll)
        dx, dy = ring[(ring.index(base) + THROW_IN_ARROWS[d]) % 8]
        nx, ny = p.x + dx, p.y + dy
        if not in_bounds(nx, ny):
            rec.emit(
                Event(kind="note", actor=p.id, rolls=[roll], text=f"{p.name()} lurches off the pitch at ({nx},{ny}).")
            )
            from ..injury import injure_by_crowd

            rec.absorb(injure_by_crowd(match, p, dice))
            return True
        if match.at(nx, ny) is not None:
            rec.emit(
                Event(kind="note", actor=p.id, rolls=[roll], text=f"{p.name()} is brought up short at ({nx},{ny}).")
            )
            break
        rec.emit(
            Event(
                kind="player_moved",
                actor=p.id,
                detail={"x": nx, "y": ny, "ma_used": p.ma_used + 1},
                rolls=[roll],
                text=f"{p.name()} is dragged to ({nx},{ny}). No Dodge is needed — they pass automatically.",
            )
        )
        if match.ball.in_play and not match.ball.carrier and (match.ball.x, match.ball.y) == (nx, ny):
            events, turned = pick_up(match, p, dice)
            rec.absorb(events)
            if turned:
                return True
    return False


def _throw_bomb(match: Match, cmd: dict, dice, rec: Recorder):
    """BOMBARDIER: "they throw a bomb IN THE SAME MANNER as when a player performs
    a Pass Action, following all the usual rules for a Pass Action. Though this is
    NOT a Pass Action itself … A player that declared a Throw Bomb Special Action
    MAY NOT PERFORM A MOVE ACTION before throwing the bomb. If at any point a bomb
    comes to rest on the ground then it will immediately explode in that square …
    When a bomb explodes, any player in the square it exploded in is hit …
    Additionally, roll a D6 for each player ADJACENT to the square … On a 4+, they
    are hit. Any Standing player that is hit is immediately Knocked Down.
    Additionally, make an Armour Roll for any Prone or Stunned players hit."

    The bomb is thrown with the Pass Action's own machinery — same ruler, same
    accuracy, same interception corridor — and then explodes wherever it stops.
    Modelled here as: throw, land, explode; the catch-and-throw-again chain is
    reported rather than followed, because it needs a coach at the other end.
    """
    from ..injury import knock_down, risk_injury
    from ..ruler import band
    from ..rules import adjacent

    p = match.by_id(str(cmd["player"]))
    x, y = int(cmd.get("x") or 0), int(cmd.get("y") or 0)
    reach = band(p.x, p.y, x, y)
    if reach is None:
        rec.emit(Event(kind="note", actor=p.id, text=f"({x},{y}) is out of range for a bomb."))
        return False

    from ..actions.throw import _passing_target
    from ..dice import roll_target
    from ..skills import roll_modifier

    bctx = roll_modifier(match, p, "throw_bomb", base=reach[1], range=reach[0])
    r = roll_target(
        dice,
        "Throw Bomb",
        _passing_target(p),
        bctx.value,
        note=reach[0] + "".join(f" · {n}" for n in bctx.notes),
    )
    rec.emit(Event(kind="note", actor=p.id, rolls=[r], text=f"{p.name()} lobs a bomb. {r.describe()}"))
    # "Should a bomb be fumbled by the thrower … it will not Bounce and will
    # instead explode in that player's square."
    bx, by = (x, y) if r.passed else (p.x, p.y)

    rec.emit(Event(kind="note", detail={"x": bx, "y": by}, text=f"The bomb explodes at ({bx},{by})!"))
    hit = [q for q in match.on_pitch() if (q.x, q.y) == (bx, by)]
    for q in match.on_pitch():
        if (q.x, q.y) == (bx, by) or not adjacent(q.x, q.y, bx, by):
            continue
        d = roll_target(dice, "Blast", 4, note=q.name())
        rec.emit(Event(kind="note", actor=q.id, rolls=[d], text=f"{q.name()} — {d.describe()}"))
        if d.passed:
            hit.append(q)
    for q in hit:
        if q.down == "standing":
            rec.absorb(knock_down(match, q, dice, cause="caught by the blast"))
        else:
            rec.absorb(risk_injury(match, q, dice))
    return any(q.side == match.clock.active for q in hit)


def _make(action: str):
    def validate(match, cmd, _a=action):
        return _validate(_a, match, cmd)

    def resolve(match, cmd, dice, _a=action):
        return _resolve(_a, match, cmd, dice)

    return validate, resolve


for _name in BY_ACTION:
    register(_name, *_make(_name))


__all__ = ["BY_ACTION", "SPECIALS", "injury_roll"]
