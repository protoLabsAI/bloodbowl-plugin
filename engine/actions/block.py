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
from .. import rerolls as team_rerolls
from ..ball import bounce, check_touchdown
from ..dice import BLOCK_LABELS, roll_target
from ..events import Event
from ..injury import injure_by_crowd, knock_down, place_prone
from ..rules import (
    MAX_RUSHES,
    adjacent,
    assist_count,
    block_dice,
    has_tackle_zone,
    push_squares,
    shares_keyword,
    strength_of,
    trait_parameter,
)
from ..skills import SkillContext, can_use, hooks_for, unmodelled_skills
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
    # A MULTIPLE BLOCK's two halves "happen SIMULTANEOUSLY … BOTH Block Actions are
    # resolved in full, EVEN IF ONE OF THEM RESULTS IN A TURNOVER". The engine
    # rolls them one after the other — which the rules explicitly permit, "you may
    # wish to roll them separately for clarity" — so the second is thrown from the
    # state the pair was DECLARED in. That is exactly the three preconditions
    # below: standing, not yet acted, and still projecting a Tackle Zone. All
    # three are true at declaration and any of them can be false by the time the
    # second roll happens, which is the whole point of the clause.
    simultaneous = bool(cmd.get("_multi"))
    if p.down != "standing" and not simultaneous:
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
    if p.acted and not blitzing and not simultaneous:
        return Legality(False, f"{p.name()} has already acted this turn")

    if not adjacent(p.x, p.y, t.x, t.y):
        return Legality(False, "a Block targets a player you are Marking — they must be adjacent")
    if not has_tackle_zone(p) and not simultaneous:
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
    # Cheering Fans: "the FIRST Block Action performed during the Coach with the
    # highest roll's next Turn receives an additional Offensive Assist." First,
    # so it is gone once anyone has blocked this turn.
    cheered = bool(match.cheer.get("ready")) and match.cheer.get("side") == p.side
    off += 1 if cheered else 0
    horns = 1 if (p.has_skill("Horns") and blitzing) else 0
    a_st, d_st = strength_of(match, p) + off + horns, strength_of(match, t) + dfn
    n, chooser = block_dice(a_st, d_st)
    # DAUNTLESS is a ROLL, so validate cannot make it — validate is asked freely
    # and must never touch the dice. It reports that the roll is coming and what
    # it would do, and resolve makes it. Reporting odds that resolve then ignores
    # would be worse than not reporting them.
    dauntless = p.has_skill("Dauntless") and strength_of(match, t) > strength_of(match, p)
    return Legality(
        True,
        "",
        {
            "dice": n,
            "chooser": chooser,
            "horns": horns,
            "dauntless": dauntless,
            "attacker_strength": a_st,
            "defender_strength": d_st,
            "offensive_assists": off,
            "defensive_assists": dfn,
            "cheered": cheered,
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


def declared_a_block(match: Match, blocker) -> bool:
    """Was a BLOCK Action declared, as opposed to a Blitz that contains one?

    S3 spells this out and gives the worked example, because it decides several
    Skills and reads like a technicality until it costs you one:

        "An action is declared at the start of a player's activation… Rules that
         come into play when an action is declared will take effect at this time —
         SO LONG AS THE DECLARED ACTION MATCHES the action in the relevant rule.
         … During a Blitz Action: a rule that comes into play when a player
         DECLARES a Block Action would not come into effect, as no Block Action
         has been declared — the declared Action was a Blitz Action. A rule that
         comes into play when a player PERFORMS a Block Action would come into
         effect."

    So a Skill whose text says "declares a Block Action" — Grab, Brawler, Multiple
    Block — is switched OFF during a Blitz, while one that says "performs" —
    Tackle, Dauntless, Wrestle, Claws, Fend — is not. Read the verb; it is load
    bearing.
    """
    b = match.blitz
    return not (bool(b) and b.get("player") == blocker.id)


def _juggernaut_suppresses(match: Match, blocker) -> bool:
    """JUGGERNAUT: "when this player performs a Block Action as part of a Blitz
    Action, opposition players cannot use the Fend, Stand Firm or Wrestle Skills."

    Only as part of a BLITZ — a Juggernaut throwing an ordinary Block suppresses
    nothing, which is the half that is easy to lose.
    """
    b = match.blitz
    return blocker.has_skill("Juggernaut") and bool(b) and b.get("player") == blocker.id


def _free_around(match: Match, target) -> list[tuple[int, int]]:
    """Every unoccupied square on the pitch adjacent to ``target``.

    Grab and Sidestep both widen the choice from the three-square arc to this,
    and both say the same thing when it is empty: "If there are no adjacent
    unoccupied squares, then this Skill cannot be used."
    """
    return [
        (target.x + dx, target.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
        and in_bounds(target.x + dx, target.y + dy)
        and match.at(target.x + dx, target.y + dy) is None
    ]


def _push_to(match: Match, blocker, target, prefer: tuple | None = None, sidestep_to: tuple | None = None):
    """Where a pushed player ends up, and whether that is off the pitch.

    Returns (square, kind) with kind in {"empty", "crowd", "occupied"}. A square
    off the pitch is the Crowd; an occupied one starts a Chain Push.

    Two Skills widen the arc, and they contest each other:

      GRAB    "this player's Coach may choose ANY unoccupied square adjacent to
              the target … Additionally, when this player performs a Block Action,
              opposition players cannot use the Sidestep Skill."
      SIDESTEP "instead of the OPPOSING Coach choosing where this player is Pushed
              Back to, this player's Coach may choose any adjacent unoccupied
              square."

    Grab belongs to the ACTING coach, so it honours ``push_to`` — the same command
    field an ordinary push already takes, just with more squares to reach.

    Sidestep belongs to the player being SHOVED, which is a different coach, so it
    has its own field: ``sidestep_to``. When nobody names a square the engine takes
    the one FURTHEST from the blocker, which is what a coach being pushed almost
    always wants — but the choice is theirs to make and the field is how they make
    it. Same for ``stand_firm``: refusing a push is a decision, and the engine only
    takes it unasked when the alternative is the Crowd.
    """
    options = push_squares(blocker.x, blocker.y, target.x, target.y)
    on_pitch = [(x, y) for x, y in options if in_bounds(x, y)]
    empty = [sq for sq in on_pitch if match.at(*sq) is None]

    # Grab reads "When this player DECLARES a Block Action", so it is off during a
    # Blitz — see declared_a_block.
    if blocker.has_skill("Grab") and declared_a_block(match, blocker):
        wider = _free_around(match, target)
        if wider:
            if prefer and tuple(prefer) in wider:
                return tuple(prefer), "empty"
            empty = wider or empty
    elif target.has_skill("Sidestep"):
        wider = _free_around(match, target)
        if wider:
            # SIDESTEP belongs to the player being SHOVED — "instead of the OPPOSING
            # Coach choosing … THIS player's Coach may choose any adjacent
            # unoccupied square." So `sidestep_to` is the DEFENDER's field, distinct
            # from `push_to` which is the attacker's: naming a square here is the
            # defending coach exercising their Skill, and the engine's
            # furthest-from-the-blocker pick is the default when nobody says.
            if sidestep_to and tuple(sidestep_to) in wider:
                return tuple(sidestep_to), "empty"
            away = max(wider, key=lambda sq: max(abs(sq[0] - blocker.x), abs(sq[1] - blocker.y)))
            return away, "empty"

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


def _do_push(
    match: Match,
    blocker,
    target,
    dice,
    rec: Recorder,
    pushed: list,
    prefer=None,
    depth: int = 0,
    sidestep_to=None,
    stand_firm=None,
) -> None:
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

    square, kind = _push_to(match, blocker, target, prefer, sidestep_to=sidestep_to)

    # Take Root: "cannot be Pushed Back, and may not leave their current square
    # for any reason". Unlike Stand Firm this is not a choice, so it comes first
    # and no note about declining is owed.
    if target.rooted:
        rec.emit(
            Event(
                kind="note",
                actor=target.id,
                detail={"skill": "Take Root"},
                text=f"{target.name()} is Rooted and cannot be Pushed Back.",
            )
        )
        return

    # STAND FIRM: "When this player would be Pushed Back during a Block Action,
    # including during a Chain Push, they can choose to not be Pushed Back and
    # instead remain in their current square."
    #
    # It is a CHOICE, and it belongs to the DEFENDING coach. `stand_firm` is how
    # they make it — True to refuse the push, False to accept one they would
    # otherwise have refused. Unset, the engine takes it in the one case where the
    # alternative is unambiguously worse: the Crowd, which is an Injury Roll with
    # no armour behind it. Anywhere else, staying put versus being shoved is
    # positional, and a default that guessed would be worse than one that says so.
    if target.has_skill("Stand Firm") and not _juggernaut_suppresses(match, blocker):
        if stand_firm is not None:
            if stand_firm:
                rec.emit(
                    Event(
                        kind="note",
                        actor=target.id,
                        detail={"skill": "Stand Firm", "used": True},
                        text=f"{target.name()} uses Stand Firm and stays exactly where they are.",
                    )
                )
                return
            rec.emit(
                Event(
                    kind="note",
                    actor=target.id,
                    detail={"skill": "Stand Firm", "used": False},
                    text=f"{target.name()} has Stand Firm and takes the push anyway.",
                )
            )
        elif kind == "crowd":
            rec.emit(
                Event(
                    kind="note",
                    actor=target.id,
                    detail={"skill": "Stand Firm"},
                    text=f"{target.name()} uses Stand Firm and refuses to be pushed into the Crowd.",
                )
            )
            return
        else:
            rec.emit(
                Event(
                    kind="note",
                    actor=target.id,
                    detail={"skill": "Stand Firm", "used": False},
                    text=f"{target.name()} has Stand Firm and could refuse this push — say "
                    "stand_firm=true on the Block to use it.",
                )
            )

    if kind == "crowd":
        rec.absorb(injure_by_crowd(match, target, dice))
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


def _bad_for_us(face: str, match: Match, p) -> bool:
    """Is this result one the ACTING coach would want to spend a Team Re-roll on?

    Player Down always; Both Down only when it would actually floor them, since a
    Both Down that a Block Skill shrugs off is a good result. Anything that pushes
    or floors the target is not worth a re-roll, and spending one on it would be
    the engine deciding something expensive on the coach's behalf.
    """
    if face == "player_down":
        return True
    return face == "both_down" and _would_fall(match, p)


def _would_fall(match: Match, who) -> bool:
    """Would this player hit the ground on a Both Down? Block is the only thing
    that keeps them up, and every Both Down decision below turns on the answer."""
    return not _uses_block(match, who)[0]


def _both_down_choice(match: Match, p, t, juggernaut=None, wrestle=None) -> tuple[str, str]:
    """What to do about a Both Down, and who is doing it.

    Three Skills offer a choice here and all three say "may", so the engine needs
    a stated policy rather than a coin toss. Each is taken only when it changes
    the outcome for the player whose choice it is:

      JUGGERNAUT "When this player declares a Blitz Action, they may treat any
                result of Both Down as Pushed Back during any Block Actions they
                perform during the Blitz Action." Same test, opposite trigger:
                only on a Blitz, and only when the blocker would otherwise fall.
      WRESTLE   "…if the Both Down result is applied, this player may choose to
                use this Skill. If they do, both players are Placed Prone,
                regardless of any other Skills." Either participant may. Placed
                Prone is HARMLESS — "they aren't at risk of being caused harm" —
                so it is taken by anyone who would otherwise be Knocked Down.

    (Brawler is the third, and is applied to the DICE before this is asked — it
    re-rolls rather than reinterpreting, so it belongs beside the roll.)

    Both policies are DEFAULTS, and both are overridable: `juggernaut=` and
    `wrestle=` on the Block are how a coach says otherwise. "May" is the rules'
    word, and a stated default that cannot be refused is not the same thing.

    Returns (choice, actor id) with choice in {"", "push", "wrestle"}.
    """
    blocker_falls = _would_fall(match, p)
    jugger = p.has_skill("Juggernaut") and not declared_a_block(match, p) and juggernaut is not False
    if jugger and (juggernaut or blocker_falls):
        return "push", p.id
    for who in (t, p):
        if who.has_skill("Wrestle") and _would_fall(match, who) and not _juggernaut_suppresses(match, p):
            return "wrestle", who.id
    return "", ""


# Best to worst FOR THE ATTACKER. POW puts the target down and leaves the attacker
# standing; Stumble does the same unless the target has Dodge, in which case it is
# a push; Push Back moves them and hurts nobody; Both Down puts BOTH down unless
# the attacker has Block or Wrestle; Player Down is the attacker alone on the floor.
_BEST_FOR_ATTACKER = ["pow", "stumble", "push_back", "both_down", "player_down"]
_WORST_FOR_ATTACKER = ["player_down", "both_down", "push_back", "stumble", "pow"]


def _choose(faces: list[str], chooser: str, acting: str, choice, match=None, blocker=None) -> int:
    """Which die is applied.

    The stronger coach chooses. When that is the ACTING coach we honour their pick
    — and when they DID NOT MAKE ONE, we pick the best face for them.

    That default used to be `faces[0]`, the first die ROLLED, which is nobody's
    choice at all: a coach who rolled "Both Down, Push Back, Push Back" and did not
    pass an index got the Both Down and went down with their target. It cost the
    agent two turns in its first live game, and it apologised for the engine's bug
    both times. An arbitrary default is worse than either honest option, because it
    looks like a decision.

    The defending branch already played the opposition as well as it could. This is
    the same courtesy pointed the other way.
    """
    if chooser == "attacker" and acting == "attacker":
        if choice is not None and str(choice).strip() != "":
            try:
                return max(0, min(int(choice), len(faces) - 1))
            except (TypeError, ValueError):
                pass
        order = list(_BEST_FOR_ATTACKER)
        # A blocker who will not fall on a Both Down (Block, or Wrestle against a
        # standing target) should take it over a mere push: the target goes down.
        if match is not None and blocker is not None and not _would_fall(match, blocker):
            order = ["pow", "both_down", "stumble", "push_back", "player_down"]
        return min(range(len(faces)), key=lambda i: order.index(faces[i]))
    return min(range(len(faces)), key=lambda i: _WORST_FOR_ATTACKER.index(faces[i]))


def resolve(match: Match, cmd: dict, dice) -> Outcome:
    # MULTIPLE BLOCK is two Block Actions, so it is decided BEFORE the first one
    # starts: "they may perform TWO Block Actions each targeting A DIFFERENT
    # opposition player THEY ARE MARKING. If they do, then this player will REDUCE
    # THEIR STRENGTH CHARACTERISTIC BY 2 for the duration … These Block Actions
    # happen SIMULTANEOUSLY … BOTH Block Actions are resolved in full, EVEN IF ONE
    # OF THEM RESULTS IN A TURNOVER. This player CANNOT FOLLOW-UP during either."
    if cmd.get("second_target") and not cmd.get("_multi"):
        return _multiple_block(match, cmd, dice)

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
    if legal.detail.get("cheered"):
        rec.emit(
            Event(
                kind="kickoff_bonus",
                actor=p.id,
                detail={"side": p.side, "cheer_used": True},
                text=f"The crowd lends {p.name()} an extra Offensive Assist.",
            )
        )

    # DAUNTLESS: "this player may roll a D6 and add their own Strength
    # Characteristic. If the result is HIGHER than the opposition player's
    # UNMODIFIED Strength Characteristic, then this player increases their
    # unmodified Strength Characteristic to match the opposition player for the
    # duration of the Block Action. Modifiers are then applied as normal."
    #
    # Match, not exceed — so it equalises Strength and no more, and the assists on
    # both sides are then re-counted against the new number.
    if legal.detail.get("dauntless"):
        theirs = strength_of(match, t)
        dr = roll_target(dice, "Dauntless", theirs + 1, modifier=strength_of(match, p), note=f"beat ST {theirs}")
        if dr.passed:
            off, dfn = legal.detail["offensive_assists"], legal.detail["defensive_assists"]
            n, chooser = block_dice(theirs + off + legal.detail["horns"], theirs + dfn)
            rec.emit(
                Event(
                    kind="note",
                    actor=p.id,
                    rolls=[dr],
                    detail={"skill": "Dauntless", "strength": theirs, "dice": n},
                    text=f"{p.name()} uses Dauntless and matches ST {theirs} — {n} dice, {chooser} chooses. "
                    f"{dr.describe()}",
                )
            )
        else:
            rec.emit(
                Event(
                    kind="note",
                    actor=p.id,
                    rolls=[dr],
                    detail={"skill": "Dauntless"},
                    text=f"{p.name()} fails the Dauntless roll. {dr.describe()}",
                )
            )

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

    # TRICKSTER: "BEFORE DETERMINING HOW MANY DICE ARE ROLLED, this player may be
    # removed from the pitch and placed in ANY OTHER UNOCCUPIED SQUARE ADJACENT TO
    # THE PLAYER PERFORMING THE ACTION." Before the dice are COUNTED, so it changes
    # the assists rather than escaping the Block — they are still adjacent, still
    # Blocked, just somewhere better. Which is why it has to happen up here, above
    # the strength comparison, rather than beside the other reactions.
    if can_use(t, "Trickster"):
        spot = _fewest_assists(match, p, t, cmd.get("trickster_to"))
        if spot is not None:
            rec.emit(
                Event(
                    kind="player_pushed",
                    actor=t.id,
                    detail={"x": spot[0], "y": spot[1], "skill": "Trickster"},
                    text=f"{t.name()} slips round to ({spot[0]},{spot[1]}) before the dice are counted.",
                )
            )
            # Re-count from the new square: that is the entire point of the move.
            fresh = validate(match, {**cmd, "_multi": True})
            if fresh.ok:
                n, chooser = fresh.detail["dice"], fresh.detail["chooser"]

    # DUMP-OFF: "this player may immediately perform a QUICK PASS BEFORE the Action
    # targeting them is resolved. This Quick Pass CANNOT CAUSE A TURNOVER … Once
    # the Quick Pass has been resolved, this Action targeting this player
    # CONTINUES." The ball leaving before the hit lands is the whole point.
    if can_use(t, "Dump-off") and match.ball.carrier == t.id:
        mate = _dump_off_target(match, t)
        if mate is not None:
            from . import get as _get

            rec.emit(
                Event(
                    kind="note",
                    actor=t.id,
                    detail={"skill": "Dump-off", "target": mate.id},
                    text=f"{t.name()} dumps the ball off to {mate.name()} before the hit lands.",
                )
            )
            was_active = match.clock.active
            try:
                # The Pass is thrown by the player being Blocked, on the opponent's
                # turn — so the clock has to say so for the Action's own checks, and
                # is put straight back.
                match.clock.active = t.side
                dumped = _get("pass")["resolve"](match, {"player": t.id, "x": mate.x, "y": mate.y}, dice)
            finally:
                match.clock.active = was_active
            rec.absorb(dumped.events)  # "cannot cause a Turnover" — so its turnover is dropped

    # FOUL APPEARANCE: "Whenever an opposition player attempts to perform a Block
    # Action against this player … they must roll a D6 BEFORE ANY OTHER DICE ARE
    # ROLLED. On a 2+, the Block Action continues as normal. On a 1, the Block
    # Action is IMMEDIATELY CANCELLED and the opposition player's activation
    # immediately ends." Not a Turnover — the Block simply does not happen, which
    # for a Blitz means the team's one Blitz is gone for nothing.
    if can_use(t, "Foul Appearance"):
        d = dice.d6()
        fa = Roll(kind="Foul Appearance", dice=[d], total=d, target=2, passed=d >= 2)
        dice.rolls.append(fa)
        rec.emit(
            Event(
                kind="note",
                actor=t.id,
                rolls=[fa],
                detail={"skill": "Foul Appearance", "cancelled": not fa.passed},
                text=f"{p.name()} recoils from {t.name()}. {fa.describe()}"
                + ("" if fa.passed else f" The Block is cancelled and {p.name()}'s activation ends."),
            )
        )
        if not fa.passed:
            rec.emit(ended(p.id, "block"))
            return Outcome(
                ok=False,
                events=rec.events,
                text=f"{p.name()} could not bring themselves to Block {t.name()} — their activation ends.",
                unmodelled=unmodelled,
            )

    faces = dice.block(n)
    face = faces[_choose(faces, chooser, "attacker", cmd.get("choice"), match, p)]
    roll = Roll(kind="Block", dice=list(faces), note=f"{n} dice, {chooser} chooses")
    dice.rolls.append(roll)
    rolls = [roll]

    # BRAWLER re-rolls before anything is applied, because it re-rolls the DICE
    # rather than changing the result: "they may re-roll a single Both Down
    # result." Once — a second Both Down stands. It reads "declares a Block
    # Action", so a Blitz switches it off.
    if face == "both_down" and p.has_skill("Brawler") and declared_a_block(match, p) and _would_fall(match, p):
        faces = dice.block(n)
        face = faces[_choose(faces, chooser, "attacker", cmd.get("choice"), match, p)]
        again = Roll(kind="Block (re-roll)", dice=list(faces), note="Brawler, a single Both Down")
        dice.rolls.append(again)
        rolls.append(again)
    # HATRED: "…this player may re-roll A SINGLE PLAYER DOWN RESULT." Brawler's
    # twin on the other bad face, and free like Brawler is — so it goes before any
    # Team Re-roll is considered.
    elif (
        face == "player_down"
        and can_use(p, "Hatred")
        and shares_keyword(p, t, trait_parameter(p, "Hatred"))
        and not cmd.get("_hated")
    ):
        faces = dice.block(n)
        face = faces[_choose(faces, chooser, "attacker", cmd.get("choice"), match, p)]
        again = Roll(kind="Block (re-roll)", dice=list(faces), note="Hatred, a single Player Down")
        dice.rolls.append(again)
        rolls.append(again)
        cmd = {**cmd, "_hated": True}

    elif _bad_for_us(face, match, p) and cmd.get("team_reroll") and team_rerolls.spend(match, p, "Block", dice, rec):
        # "When a Team Re-roll is used to re-roll a dice pool, ALL THE DICE IN THE
        # POOL must be re-rolled" — so the whole handful goes again, not the one
        # face that was applied.
        faces = dice.block(n)
        face = faces[_choose(faces, chooser, "attacker", cmd.get("choice"), match, p)]
        again = Roll(kind="Block (Team Re-roll)", dice=list(faces), note=f"all {n} dice re-rolled")
        dice.rolls.append(again)
        rolls.append(again)

    first = rec.emit(
        Event(
            kind="block_rolled",
            actor=p.id,
            detail={"faces": list(faces), "chosen": face, "chooser": chooser, "target": t.id, "blitz": blitzing},
            rolls=rolls,
            text=f"{p.name()} {'Blitzes' if blitzing else 'Blocks'} {t.name()}: "
            + ", ".join(BLOCK_LABELS[f] for f in faces)
            + f" — {BLOCK_LABELS[face]} applied ({chooser} chooses)."
            + (" Brawler re-rolled the Both Down." if len(rolls) > 1 else ""),
        )
    )

    turnover = False
    pushed: list = []
    follow_up = bool(cmd.get("follow_up", True))
    # "when this player performs a Block Action against an opposition player, the
    # opposition player does not count as having the Dodge Skill if a Stumble
    # result is selected" — the blocker's Tackle, not the target's anything.
    tackled = SkillContext(match=match, player=t)
    for skill, fn in hooks_for("deny_dodge_skill"):
        if p.has_skill(skill):
            fn(tackled)
    dodged = face == "stumble" and t.has_skill("Dodge") and not tackled.flags.get("denied")

    def _push_and_follow(knock_after: bool) -> None:
        vacated = (t.x, t.y)
        had_ball = match.ball.carrier == t.id
        _do_push(
            match,
            p,
            t,
            dice,
            rec,
            pushed,
            prefer=cmd.get("push_to"),
            sidestep_to=cmd.get("sidestep_to"),
            stand_firm=cmd.get("stand_firm"),
        )
        # STRIP BALL: "if an opposition player is Pushed Back then they will DROP
        # THE BALL IN THE SQUARE THEY ARE PUSHED BACK INTO, at which point it will
        # Bounce from that square. This Bounce will happen BEFORE the opposition
        # player becomes Prone (if applicable) but AFTER this player chooses to
        # Follow-up." The bounce is deferred to the end of this function for
        # exactly that reason — it is one of the few orderings the rules spell out.
        stripped = had_ball and t.place == "pitch" and can_use(p, "Strip Ball")
        # "Apply the Push Back result. The target is then Knocked Down in the
        # square they are now in" — so the Armour Roll happens where they land.
        #
        # SABOTEUR goes first: "When THIS PLAYER IS KNOCKED DOWN as a result of AN
        # OPPOSITION PLAYER'S Block Action, BEFORE THE ARMOUR ROLL IS MADE … On a
        # 4+ … the opposition player is ALSO Knocked Down … this player is
        # AUTOMATICALLY KNOCKED OUT and the Armour Roll is NOT MADE for them." A
        # trade, not a save.
        if knock_after and t.place == "pitch" and not _saboteur(match, t, p, dice, rec):
            rec.absorb(knock_down(match, t, dice, by=p))
        # FEND: "When a player with this Skill is Pushed Back as a result of a
        # Block Action performed against them, then the opposition player MAY NOT
        # Follow-up." Not optional, and not a preference the acting coach can
        # override — which is why it is checked after `follow_up`, not folded into
        # it. The exception is a Juggernaut mid-Blitz.
        # TAUNT: "this player's Coach may CHOOSE TO MAKE the opposition player
        # Follow-up." The defender forcing the attacker forward, which is the
        # opposite of Fend and is why the two cannot both apply. Taken whenever it
        # is available: a defender who taunts wants the attacker off their line,
        # and there is nobody at the table to ask.
        taunted = can_use(t, "Taunt") and not (t.rooted or p.rooted)
        if taunted and not follow_up:
            rec.emit(
                Event(
                    kind="note",
                    actor=t.id,
                    detail={"skill": "Taunt"},
                    text=f"{t.name()} jeers — {p.name()} must Follow-up whether they meant to or not.",
                )
            )
        fended = t.has_skill("Fend") and not _juggernaut_suppresses(match, p) and not taunted
        if fended and follow_up:
            rec.emit(
                Event(
                    kind="note",
                    actor=t.id,
                    detail={"skill": "Fend"},
                    text=f"{t.name()} uses Fend — {p.name()} may not Follow-up.",
                )
            )
        # "…may not Follow-up after performing a Block Action" while Rooted.
        if p.rooted and follow_up:
            rec.emit(
                Event(
                    kind="note",
                    actor=p.id,
                    detail={"skill": "Take Root"},
                    text=f"{p.name()} is Rooted and may not Follow-up.",
                )
            )
        if (follow_up or taunted) and not fended and not p.rooted and match.at(*vacated) is None:
            rec.emit(
                Event(
                    kind="player_followed_up",
                    actor=p.id,
                    detail={"x": vacated[0], "y": vacated[1]},
                    text=f"{p.name()} follows up to ({vacated[0]},{vacated[1]}).",
                )
            )
        # EYE GOUGE: "When an opposition player is Pushed Back by this player, the
        # opposition player CANNOT PROVIDE OFFENSIVE OR DEFENSIVE ASSISTS UNTIL
        # AFTER THEY ARE NEXT ACTIVATED." Distracted is exactly that duration —
        # "they will remain Distracted until they are next activated" — and it
        # already removes a Tackle Zone, which is what an assist needs.
        if can_use(p, "Eye Gouge") and t.place == "pitch":
            rec.emit(
                Event(
                    kind="player_status",
                    actor=t.id,
                    detail={"distracted": True, "skill": "Eye Gouge"},
                    text=f"{p.name()} gets a thumb in {t.name()}'s eye — no assists until they are next activated.",
                )
            )

        # …and NOW the Strip Ball bounce, after the Follow-up, exactly as written.
        if stripped and match.ball.carrier == t.id:
            rec.emit(
                Event(
                    kind="ball_dropped",
                    actor=t.id,
                    detail={"x": t.x, "y": t.y, "skill": "Strip Ball"},
                    text=f"Strip Ball: {p.name()} knocks the ball out of {t.name()}'s hands.",
                )
            )
            rec.absorb(bounce(match, dice))

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
        # The BLOCKER goes down. Saboteur is not here: it fires when a player is
        # knocked down "as a result of AN OPPOSITION PLAYER'S Block Action", and a
        # Player Down is the blocker falling over their own Block.
        if not _saboteur(match, p, t, dice, rec):
            rec.absorb(knock_down(match, p, dice, by=t))
        turnover = True

    elif face == "both_down":
        choice, actor = _both_down_choice(match, p, t, juggernaut=cmd.get("juggernaut"), wrestle=cmd.get("wrestle"))

        if choice == "push":
            rec.emit(
                Event(
                    kind="note",
                    actor=p.id,
                    detail={"skill": "Juggernaut"},
                    text=f"{p.name()} uses Juggernaut and treats the Both Down as a Push Back.",
                )
            )
            _push_and_follow(knock_after=False)

        elif choice == "wrestle":
            # "both players in the Block Action are Placed Prone, regardless of any
            # other Skills they may possess" — and Placed Prone is harmless, so
            # neither of them rolls armour. That is the whole point of the Skill.
            who = match.by_id(actor)
            rec.emit(
                Event(
                    kind="note",
                    actor=actor,
                    detail={"skill": "Wrestle"},
                    text=f"{who.name()} uses Wrestle — both players are Placed Prone, and neither is at risk.",
                )
            )
            for each in (p, t):
                rec.absorb(place_prone(match, each, dice, reason="Wrestle"))
            turnover = True  # the active team's player is on the floor

        else:
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

    # PILE DRIVER: "When an opposition player is KNOCKED DOWN by this player during
    # a Block Action, this player MAY perform a FREE FOUL ACTION against the
    # opposition player SO LONG AS THEY ARE STILL STANDING AND ARE STILL MARKING
    # the opposition player. This player is then PLACED PRONE and their activation
    # immediately ends."
    #
    # Placed Prone rather than Knocked Down, so it costs them their feet but not an
    # Armour Roll — and the activation ends whatever the Foul achieved. Taken
    # whenever it is available: it is a free Foul, and its cost is one the coach
    # accepted when they threw the Block.
    if (
        can_use(p, "Pile Driver")
        and t.place == "pitch"
        and t.down != "standing"
        and p.down == "standing"
        and p.place == "pitch"
        and adjacent(p.x, p.y, t.x, t.y)
    ):
        from . import get as _get_action

        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                detail={"skill": "Pile Driver", "target": t.id},
                text=f"{p.name()} drops a knee on {t.name()} — a free Foul, and then they are down too.",
            )
        )
        free = _get_action("foul")["resolve"](match, {"player": p.id, "target": t.id}, dice)
        rec.absorb(free.events)
        # The Foul's own sending-off is a Turnover; the Pile Driver itself is not.
        turnover = turnover or free.turnover
        if p.place == "pitch" and p.down == "standing":
            rec.absorb(place_prone(match, p, dice, reason="Pile Driver"))
        rec.emit(ended(p.id, "block", f"{p.name()}'s activation ends after the Pile Driver."))
        return Outcome(
            ok=not turnover,
            events=rec.events,
            turnover=turnover,
            text=first.text,
            unmodelled=unmodelled,
        )

    # HIT AND RUN: "after FULLY RESOLVING the Action, they may immediately move ONE
    # FREE SQUARE IGNORING TACKLE ZONES, so long as they are STILL STANDING. The
    # player must ensure that AFTER THIS FREE MOVE THEY ARE NOT MARKED BY OR
    # MARKING ANY OPPOSITION PLAYERS."
    #
    # That last sentence is the constraint that makes it a retreat rather than a
    # reposition: there has to BE a square with nobody adjacent, or the Skill
    # cannot be used at all.
    if can_use(p, "Hit and Run") and not turnover and p.down == "standing" and p.place == "pitch":
        away = _clear_square(match, p)
        if away is not None:
            rec.emit(
                Event(
                    kind="player_pushed",
                    actor=p.id,
                    detail={"x": away[0], "y": away[1], "skill": "Hit and Run"},
                    text=f"Hit and Run: {p.name()} steps away to ({away[0]},{away[1]}), clear of everyone.",
                )
            )

    # FRENZY: "if after the target is Pushed Back they are STILL STANDING, then
    # this player MUST perform A SECOND Block Action targeting THE SAME opposition
    # player." Must — it is not a choice, and it is the one Skill that makes the
    # engine take an Action nobody asked for. Exactly one extra: "a second Block
    # Action", so the recursion is one deep and `_frenzied` says so.
    if (
        can_use(p, "Frenzy")
        and not cmd.get("_frenzied")
        and not cmd.get("_multi")
        and not turnover
        and p.down == "standing"
        and p.place == "pitch"
        and t.place == "pitch"
        and t.down == "standing"
        and any(e.kind in ("player_pushed", "player_followed_up") for e in rec.events)
        and adjacent(p.x, p.y, t.x, t.y)
    ):
        rec.emit(
            Event(
                kind="note",
                actor=p.id,
                detail={"skill": "Frenzy", "target": t.id},
                text=f"{p.name()} is in a Frenzy and MUST Block {t.name()} again.",
            )
        )
        second = resolve(match, {**cmd, "_frenzied": True, "follow_up": True}, dice)
        rec.absorb(second.events)
        return Outcome(
            ok=second.ok,
            events=rec.events,
            turnover=second.turnover,
            text=first.text,
            unmodelled=unmodelled,
        )

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
    elif not cmd.get("_multi"):
        rec.emit(ended(p.id, "blitz" if blitzing else "block", f"{p.name()}'s Block Action is over."))

    return Outcome(
        ok=not turnover,
        events=rec.events,
        turnover=turnover,
        text=first.text,
        unmodelled=unmodelled,
    )


def _fewest_assists(match: Match, blocker, p, want=None) -> tuple[int, int] | None:
    """Trickster's landing square: adjacent to the blocker, unoccupied, and with as
    few opposition assists reaching it as possible.

    "Any other unoccupied square" is a coach's choice with no rule to guide it, and
    it belongs to the player being Blocked. ``want`` is that choice; without one
    the engine states a policy — fewest assists is the only thing about the square
    that changes the Block, which is what the Trait is for.
    """
    from ...pitch import in_bounds as _in

    if want:
        wx, wy = int(want[0]), int(want[1])
        if _in(wx, wy) and match.at(wx, wy) is None and max(abs(wx - blocker.x), abs(wy - blocker.y)) == 1:
            return (wx, wy)

    best, chosen = None, None
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if not dx and not dy:
                continue
            x, y = blocker.x + dx, blocker.y + dy
            if not _in(x, y) or match.at(x, y) is not None or (x, y) == (p.x, p.y):
                continue
            helpers = sum(
                1
                for q in match.on_pitch(blocker.side)
                if q.id != blocker.id and has_tackle_zone(q) and adjacent(q.x, q.y, x, y)
            )
            if best is None or helpers < best:
                best, chosen = helpers, (x, y)
    return chosen


def _dump_off_target(match: Match, carrier):
    """Who the Dump-off goes to: the nearest team-mate within a Quick Pass who is
    Standing and free to catch. None if there is nobody, in which case the Skill
    simply does not fire."""
    mates = [
        q
        for q in match.on_pitch(carrier.side)
        if q.id != carrier.id and q.down == "standing" and not getattr(q, "distracted", False)
    ]
    if not mates:
        return None
    near = min(mates, key=lambda q: (max(abs(q.x - carrier.x), abs(q.y - carrier.y)), q.id))
    return near if max(abs(near.x - carrier.x), abs(near.y - carrier.y)) <= 3 else None


def _saboteur(match: Match, victim, opponent, dice, rec: Recorder) -> bool:
    """The rigged weapon of the player who is ABOUT TO BE knocked down.

    Whose Skill it is, is the thing to get right: `victim` is the player going to
    the floor, `opponent` is the one who put them there. On a 4+ the opponent goes
    down too and the victim is Knocked Out with no Armour Roll — a trade, not a
    save, and the only way to spend a player deliberately.

    Returns True if it went off, in which case the caller must NOT knock the victim
    down as well: "the Armour Roll is not made for them".
    """
    from ..dice import Roll as _Roll

    if not can_use(victim, "Saboteur"):
        return False
    d = dice.d6()
    roll = _Roll(kind="Saboteur", dice=[d], total=d, target=4, passed=d >= 4)
    dice.rolls.append(roll)
    rec.emit(
        Event(
            kind="note",
            actor=victim.id,
            rolls=[roll],
            detail={"skill": "Saboteur"},
            text=f"{victim.name()}'s rigged weapon. {roll.describe()}"
            + (" It goes off." if roll.passed else " Nothing happens."),
        )
    )
    if not roll.passed:
        return False
    rec.absorb(knock_down(match, opponent, dice, by=victim, cause="caught by the sabotaged weapon"))
    rec.emit(
        Event(
            kind="player_condition",
            actor=victim.id,
            detail={"outcome": "knocked_out", "skill": "Saboteur"},
            text=f"{victim.name()} is Knocked Out by their own weapon — no Armour Roll.",
        )
    )
    return True


def _multiple_block(match: Match, cmd: dict, dice) -> Outcome:
    """Two Block Actions at once. See the quote in `resolve`.

    "EVEN IF ONE OF THEM RESULTS IN A TURNOVER" is the clause that shapes this:
    the second Block is resolved in full whatever the first did, so the turnover
    is collected and reported at the end rather than returned from the middle.
    """
    p = match.by_id(str(cmd.get("player") or ""))
    if p is None or not can_use(p, "Multiple Block"):
        return Outcome(ok=False, text="only a player with Multiple Block may name two targets")
    first_id, second_id = str(cmd.get("target") or ""), str(cmd.get("second_target") or "")
    if first_id == second_id:
        return Outcome(ok=False, text="the two Blocks must target DIFFERENT opposition players")
    targets = [match.by_id(first_id), match.by_id(second_id)]
    if any(q is None for q in targets):
        return Outcome(ok=False, text="both targets must be players in this match")
    if any(not adjacent(p.x, p.y, q.x, q.y) or q.side == p.side for q in targets):
        return Outcome(ok=False, text="a Multiple Block targets two opposition players this player is MARKING")

    rec = Recorder(match)
    rec.emit(
        Event(
            kind="note",
            actor=p.id,
            detail={"skill": "Multiple Block", "targets": [first_id, second_id]},
            text=f"{p.name()} throws themselves at {targets[0].name()} and {targets[1].name()} at once "
            "— ST -2 for both, and no Follow-up.",
        )
    )
    # "reduce their Strength Characteristic by 2 FOR THE DURATION of the Block
    # Actions" — restored afterwards however they end, which is why it is a
    # try/finally rather than an event: it is not a state a replay needs, it is an
    # argument to two rolls.
    was = p.player.ST
    turned_over = False
    try:
        p.player.ST = str(max(1, strength_of(match, p) - 2))
        for target in (first_id, second_id):
            out = resolve(match, {**cmd, "target": target, "follow_up": False, "_multi": True}, dice)
            rec.absorb(out.events)
            turned_over = turned_over or out.turnover
    finally:
        p.player.ST = was

    rec.emit(ended(p.id, "block", f"{p.name()}'s Multiple Block is over."))
    return Outcome(
        ok=not turned_over,
        events=rec.events,
        turnover=turned_over,
        text=f"{p.name()} Blocked two players at once.",
    )


def _clear_square(match: Match, p):
    """The nearest adjacent square where this player is neither Marked by nor
    Marking anybody — Hit and Run's own condition, and the reason it is a retreat
    rather than a reposition. None if there is no such square."""
    from ...pitch import in_bounds as _in

    foes = [q for q in match.on_pitch(match.opponent(p.side)) if q.down == "standing"]
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if not dx and not dy:
                continue
            x, y = p.x + dx, p.y + dy
            if not _in(x, y) or match.at(x, y) is not None:
                continue
            if not any(adjacent(q.x, q.y, x, y) for q in foes):
                return (x, y)
    return None


register("block", validate, resolve)
