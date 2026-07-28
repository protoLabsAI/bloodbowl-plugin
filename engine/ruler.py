"""The Range Ruler, modelled — and the one place this engine uses a number that
is NOT in the rules text.

Everything else in the engine is read off the S3 source and quoted. The Range
Ruler cannot be: it is a physical template, and the rules define passing entirely
in terms of laying it on the table. The text names the four sections —
"I: Quick Pass, II: Short Pass, III: Long Pass, IIII: Long Bomb" — and then defers
to a diagram. There is no official table of squares to read.

So these constants are DERIVED, and the derivation is written down so it can be
argued with:

* Section lengths come from published measurements of the physical ruler
  (3.49 squares for each of the first three sections, 2.75 for the Long Bomb).
* They were cross-checked against a SEPARATE community source that reported the
  maximum reach of each band as "N squares across, M down": Quick 3x1, Short 6x0,
  Long 10x2, Long Bomb 13x1. Under a straight centre-to-centre Euclidean measure
  every one of those four falls inside its band, and in every case the next square
  along the same line falls outside it. Two independent sources agreeing on all
  eight boundaries is the strongest validation available without the ruler in hand.
* The ruler has a WIDTH as well as a length, which is what makes interception a
  corridor rather than a line.

Both are config, so an operator with the real ruler can correct them in one place
without touching a rule. And ``BANDS_ARE_MEASURED`` is reported alongside any pass
the engine adjudicates, so a coach is never told a range is rules-derived when it
is measured.

KNOWN APPROXIMATION: the physical rule is that a square only partially under a
section counts as the FURTHER section. Measuring centre-to-centre treats a square
as a point and is therefore slightly generous at each boundary. The reported
maxima above were themselves derived from the physical ruler, so reproducing them
exactly is the best available evidence that the approximation lands in the right
place.
"""

from __future__ import annotations

import math

BANDS_ARE_MEASURED = True

# Cumulative Euclidean distance, in squares, at which each band ends.
QUICK_PASS = 3.49
SHORT_PASS = 6.98
LONG_PASS = 10.47
LONG_BOMB = 13.22

# Ruler width in squares. Half of it either side of the centre line is the
# corridor an interceptor has to be standing in.
RULER_WIDTH = 1.695

BANDS = (
    ("Quick Pass", QUICK_PASS, 0),
    ("Short Pass", SHORT_PASS, -1),
    ("Long Pass", LONG_PASS, -2),
    ("Long Bomb", LONG_BOMB, -3),
)


def configure(cfg: dict | None) -> None:
    """Let an operator with the real ruler correct the measurements."""
    global QUICK_PASS, SHORT_PASS, LONG_PASS, LONG_BOMB, RULER_WIDTH, BANDS
    cfg = (cfg or {}).get("range_ruler") or {}
    QUICK_PASS = float(cfg.get("quick_pass", QUICK_PASS))
    SHORT_PASS = float(cfg.get("short_pass", SHORT_PASS))
    LONG_PASS = float(cfg.get("long_pass", LONG_PASS))
    LONG_BOMB = float(cfg.get("long_bomb", LONG_BOMB))
    RULER_WIDTH = float(cfg.get("width", RULER_WIDTH))
    BANDS = (
        ("Quick Pass", QUICK_PASS, 0),
        ("Short Pass", SHORT_PASS, -1),
        ("Long Pass", LONG_PASS, -2),
        ("Long Bomb", LONG_BOMB, -3),
    )


def distance(ax: int, ay: int, bx: int, by: int) -> float:
    """Centre-to-centre, which is what a ruler laid on the board measures."""
    return math.hypot(ax - bx, ay - by)


def band(ax: int, ay: int, bx: int, by: int) -> tuple[str, int] | None:
    """(band name, Passing Ability modifier), or None if it is out of range."""
    d = distance(ax, ay, bx, by)
    for name, limit, modifier in BANDS:
        if d <= limit:
            return name, modifier
    return None


def in_corridor(ax: int, ay: int, bx: int, by: int, px: int, py: int) -> bool:
    """Does the ruler, laid from (ax,ay) to (bx,by), overlap square (px,py)?

    The ruler is a rectangle, so this is a perpendicular distance to the segment
    plus the half-square a cell occupies either side of its own centre. Endpoints
    are excluded: the passer is not in the way of their own throw, and the landing
    square is handled by the catch rather than by an interception.
    """
    vx, vy = bx - ax, by - ay
    length = math.hypot(vx, vy)
    if length == 0:
        return False
    # Projection of the point onto the segment, clamped to it.
    t = ((px - ax) * vx + (py - ay) * vy) / (length * length)
    if t <= 0 or t >= 1:
        return False
    cross = abs(vx * (py - ay) - vy * (px - ax)) / length
    return cross <= (RULER_WIDTH / 2) + 0.5


def describe() -> dict:
    """What the engine will say about its own ruler when asked."""
    return {
        "measured_not_quoted": BANDS_ARE_MEASURED,
        "bands": [{"name": n, "max_squares": lim, "modifier": m} for n, lim, m in BANDS],
        "width": RULER_WIDTH,
        "note": (
            "The Range Ruler is a physical template; the rules define passing by laying it on "
            "the table and give no table of squares. These limits are measured from the ruler "
            "and cross-checked against independently reported maximum reaches, not quoted from "
            "the rulebook. Correct them in config.bloodbowl.range_ruler if you have the ruler."
        ),
    }
