"""Board maths every action shares: adjacency, Tackle Zones, Marking, targets.

Kept in one place because these are the questions each action asks in the same
way, and two subtly different answers to "who is Marking this square" is the kind
of divergence nobody notices until a game plays wrong.

Every rule here was read off the S3 text rather than recalled. The ones that
differ from what an older edition (or a language model) would tell you are called
out where they sit, because those are the ones that get "corrected" back to the
wrong thing later.
"""

from __future__ import annotations

import re

from .state import Match, PlayerState, touchdown_row

STAND_UP_COST = 3  # squares of Move Allowance
STAND_UP_ROLL = 4  # MA 2 or less must roll this instead
MAX_RUSHES = 2


def adjacent(ax: int, ay: int, bx: int, by: int) -> bool:
    """The eight squares around a player — a Tackle Zone's shape."""
    if (ax, ay) == (bx, by):
        return False
    return abs(ax - bx) <= 1 and abs(ay - by) <= 1


def has_tackle_zone(p: PlayerState) -> bool:
    """Only STANDING players project a Tackle Zone.

    Prone and Stunned players do not, so walking away from a player you just
    knocked down needs no Dodge. (S3 adds a third case, Distracted: a Standing
    player that has lost its Tackle Zone. Modelled as a flag so the rest of the
    engine asks this question and not `down == "standing"`.)
    """
    return p.place == "pitch" and p.down == "standing" and not getattr(p, "distracted", False)


def markers_of_square(match: Match, side: str, x: int, y: int) -> list[PlayerState]:
    """Opposition players Marking square (x, y), from ``side``'s point of view."""
    foe = match.opponent(side)
    return [p for p in match.on_pitch(foe) if has_tackle_zone(p) and adjacent(p.x, p.y, x, y)]


def is_marked(match: Match, player: PlayerState) -> bool:
    """Is this player Marked where they stand? Marked is what forces a Dodge."""
    return bool(markers_of_square(match, player.side, player.x, player.y))


def dodge_modifier(match: Match, player: PlayerState, to_x: int, to_y: int) -> int:
    """-1 for each opposition player Marking the square being moved INTO.

    The square being LEFT does not modify the roll — only whether a Dodge is
    needed at all. Getting this backwards is the single most common way to
    mis-model Blood Bowl movement, and it produces plausible numbers either way.

    Titchy is the exception, and it belongs HERE rather than on the dodging
    player: "when an opposition player attempts to Dodge into a square within this
    player's Tackle Zone, this player will not apply a -1 modifier … for Marking
    the opposition player." The penalty is never applied, so it cannot be
    cancelled afterwards by a Skill on the dodger.
    """
    return -sum(1 for m in markers_of_square(match, player.side, to_x, to_y) if not m.has_skill("Titchy"))


def agility_target(player: PlayerState) -> int:
    """AG as a number. Roster values read "3+"."""
    m = re.search(r"\d+", str(player.player.AG or ""))
    return int(m.group(0)) if m else 4


def armour_target(player: PlayerState) -> int:
    m = re.search(r"\d+", str(player.player.AV or ""))
    return int(m.group(0)) if m else 8


def occupied(match: Match, x: int, y: int) -> bool:
    return match.at(x, y) is not None


def jump_over(match: Match, player: PlayerState, x: int, y: int) -> PlayerState | None:
    """The Prone or Stunned player a Jump to (x, y) would go over, if any.

    S3: "a player may attempt to Jump over players that are Prone or Stunned and
    into an unoccupied square beyond. A player that attempts to Jump over another
    player in this manner may attempt to Jump into an unoccupied square that is
    adjacent to the Prone or Stunned player they are attempting to Jump over, but
    IS NOT ALREADY ADJACENT TO THE JUMPING PLAYER."

    That last clause is the whole shape of it — the eligible squares are the far
    side of the body, not any square touching it — and it is what stops a Jump
    being a free diagonal step.
    """
    if match.at(x, y) is not None or not in_bounds_xy(x, y):
        return None
    if adjacent(player.x, player.y, x, y) or (player.x, player.y) == (x, y):
        return None
    for v in match.on_pitch():
        if v.id == player.id or v.down == "standing":
            continue
        if adjacent(v.x, v.y, player.x, player.y) and adjacent(v.x, v.y, x, y):
            return v
    return None


def in_bounds_xy(x: int, y: int) -> bool:
    from ..pitch import in_bounds

    return in_bounds(x, y)


def could_score_without_dice(match: Match, player: PlayerState) -> bool:
    """Could this player walk the ball in, needing no roll of any kind?

    S3 STALLING: "Should a player be in possession of the ball when they are
    activated, can score a Touchdown WITHOUT HAVING TO ROLL ANY DICE, yet finishes
    their activation without having scored … then they are said to be Stalling. If
    a player needs to roll any dice in order to score, such as having to Dodge,
    Rush, perform a Block Action, roll for a Trait … then they are NOT said to be
    Stalling."

    So the walk must be free at every step: never leaving a Marked square (that is
    a Dodge), never exceeding the Move Allowance (that is a Rush), and through
    empty squares. A breadth-first search with exactly those constraints answers
    it, and answering it any more loosely would accuse a coach of Stalling when
    they could not in fact have scored.
    """
    from ..pitch import in_bounds

    if match.ball.carrier != player.id or player.place != "pitch" or player.down != "standing":
        return False
    if any(g.has_skill(t) for g in (player,) for t in ("Bone Head", "Really Stupid", "Take Root")):
        return False  # they must roll on activation, so they are never Stalling
    goal = touchdown_row(player.side)
    budget = player.movement() - player.ma_used
    seen = {(player.x, player.y)}
    frontier = [(player.x, player.y)]
    for _ in range(max(0, budget)):
        nxt = []
        for cx, cy in frontier:
            if markers_of_square(match, player.side, cx, cy):
                continue  # leaving here needs a Dodge, which is a die
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    sq = (cx + dx, cy + dy)
                    if sq in seen or not in_bounds(*sq) or match.at(*sq) is not None:
                        continue
                    if sq[1] == goal:
                        return True
                    seen.add(sq)
                    nxt.append(sq)
        frontier = nxt
    return False


def steps_to_mark(match: Match, player: PlayerState, target: PlayerState) -> int | None:
    """Fewest Move steps for ``player`` to end up ADJACENT to ``target``.

    Board maths, not a rule — it answers "could this player get to them at all",
    which is what a Blitz declaration turns on: "Players may not declare an
    opposition player as the intended target of the Block Action if they cannot
    reach the player at all with their Move Action (including any extra squares
    gained by attempting to Rush)."

    DISTANCE, not probability. A Dodge or a Rush is a roll made FOR a step, not an
    extra step, so a route through three Tackle Zones is exactly as long as one
    through none — it is only less likely to survive. Weighting the search by risk
    would quietly refuse declarations the rules allow.

    Returns None when there is no route: a target walled in by other players is
    unreachable however much Move Allowance is going spare.
    """
    from ..pitch import in_bounds

    if adjacent(player.x, player.y, target.x, target.y):
        return 0

    goals = {
        (target.x + dx, target.y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0) and in_bounds(target.x + dx, target.y + dy)
    }
    seen = {(player.x, player.y)}
    frontier = [(player.x, player.y)]
    steps = 0
    while frontier:
        steps += 1
        nxt = []
        for x, y in frontier:
            for dx, dy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                sq = (x + dx, y + dy)
                # A square holding anyone is not a square you can move through;
                # the TARGET's own square included, which is why the goal is the
                # ring around them rather than the square itself.
                if sq in seen or not in_bounds(*sq) or match.at(*sq) is not None:
                    continue
                seen.add(sq)
                if sq in goals:
                    return steps
                nxt.append(sq)
        frontier = nxt
    return None


# --- blocking -------------------------------------------------------------

# The eight directions, in ring order, so "the two directions either side of this
# one" is a lookup rather than trigonometry.
RING = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def push_squares(bx: int, by: int, tx: int, ty: int) -> list[tuple[int, int]]:
    """The three squares a target may be Pushed Back into.

    S3: "Pushed Back one square away from the player that performed the Block
    Action so that they are in an adjacent square that is not adjacent to the
    player performing the Block Action", with diagrams.

    Implementing that sentence LITERALLY is wrong on a diagonal. Take the
    neighbours of the target that are not adjacent to the blocker: for an
    orthogonal block that is three squares and correct, but for a DIAGONAL block
    it is five, and the diagrams show three. So the direction is what defines the
    arc: the push vector, plus the two ring directions either side of it. Both
    readings agree on an orthogonal block, which is exactly why the wrong one
    survives casual testing.

    Order matters: STRAIGHT BACK first, then the two flanks. The blocking coach
    chooses which of the three is used, but when no choice is given the default
    should be the obvious one — shoving someone directly away rather than
    sideways into a diagonal for no reason.
    """
    d = (_sign(tx - bx), _sign(ty - by))
    if d == (0, 0):
        return []
    i = RING.index(d)
    return [(tx + dx, ty + dy) for dx, dy in (d, RING[(i - 1) % 8], RING[(i + 1) % 8])]


def assist_count(
    match: Match,
    assisting_side: str,
    around: PlayerState,
    exclude: frozenset | set | tuple = (),
) -> int:
    """Assists for a Block, from ``assisting_side``'s point of view.

    S3: "+1 Strength modifier for each player of the same team who is Marking the
    target of the Block Action and is not Marked by another opposing player."

    Three details, each of which was wrong at some point:

    * The assister must be able to Mark at all — Prone, Stunned and Distracted
      players have no Tackle Zone.
    * "not Marked by ANOTHER opposing player" excludes ``around`` itself, so the
      player being blocked never cancels the assists against them, and the blocker
      never cancels their own defenders' assists.
    * ``exclude`` is the two participants. Neither the blocker nor the target is an
      assist: the blocker is throwing the block, and the target cannot help
      themselves resist it. Leaving them in inflates BOTH sides by one — which
      still produces a plausible-looking "ST 5 v 4" that no one would query.
    """
    from .skills import hooks_for

    foes = match.opponent(assisting_side)
    skip = set(exclude) | {around.id}
    total = 0
    for p in match.on_pitch(assisting_side):
        if p.id in skip or not has_tackle_zone(p):
            continue
        if not adjacent(p.x, p.y, around.x, around.y):
            continue
        others = [
            q for q in match.on_pitch(foes) if q.id != around.id and has_tackle_zone(q) and adjacent(q.x, q.y, p.x, p.y)
        ]
        if others:
            # Guard ignores being Marked. Asked as a hook so a Skill that changes
            # who may assist is a registration, not a branch in here.
            free = any(p.has_skill(skill) for skill, _fn in hooks_for("may_assist_while_marked"))
            if not free:
                continue
        total += 1
    return total


def strength_of(match: Match, p: PlayerState) -> int:
    try:
        return int(str(p.player.ST).strip() or 0)
    except ValueError:
        return 0


def block_dice(attack_st: int, defend_st: int) -> tuple[int, str]:
    """(number of dice, who chooses).

    S3: equal strength rolls one die; higher rolls two; OVER DOUBLE rolls three.
    "Over double" is strictly greater than twice — 4 against 2 is two dice, not
    three. The stronger coach always chooses, which is why a block into a stronger
    opponent hands THEM the pick.
    """
    if attack_st == defend_st:
        return 1, "attacker"
    strong, weak = max(attack_st, defend_st), min(attack_st, defend_st)
    n = 3 if strong > 2 * weak else 2
    return n, ("attacker" if attack_st > defend_st else "defender")
