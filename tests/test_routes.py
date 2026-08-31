"""Where a player can get to, and the odds of arriving on their feet.

This is the arithmetic the SOUL told the coach to do and nothing gave it a way to
do: "three separate 2+ rolls is not three safe rolls, it is 58%". A number a coach
derives is a number a coach derives wrongly, so the engine derives it — the same
argument that puts the dice and the rulings here.
"""

from __future__ import annotations

import pytest
from bloodbowl.engine.dice import chance
from bloodbowl.engine.game import legal_moves, new_match, routes, situation
from bloodbowl.pitch import Player, Scenario


def board(*players, seed=3, kicking_to="home"):
    sc = Scenario(name="t", home_team="Orc", away_team="Skaven")
    sc.players = list(players)
    return new_match(sc, seed=seed, kicking_to=kicking_to)


def orc(x, y, ma="6", ag="3+"):
    return Player(side="home", x=x, y=y, position="Orc Lineman", team="Orc", MA=ma, AG=ag, AV="10+")


def rat(x, y):
    return Player(side="away", x=x, y=y, position="Skaven Clanrat", team="Skaven", MA="7", AG="3+", AV="8+")


# --- the probability itself ----------------------------------------------


@pytest.mark.parametrize(
    "target,modifier,expected,why",
    [
        (3, 0, 4 / 6, "AG 3+ into an unmarked square"),
        (3, -1, 3 / 6, "one Marker on the destination"),
        (3, -2, 2 / 6, "two Markers"),
        (2, 0, 5 / 6, "a Rush"),
        # THE TWO THAT EYEBALLED ODDS GET WRONG, both from the same clause:
        # a natural 1 always fails and a natural 6 always succeeds.
        (7, 0, 1 / 6, "needs 7+ — still lands on a natural 6, so not zero"),
        (1, 0, 5 / 6, "needs 1+ — a natural 1 still fails, so not certain"),
    ],
)
def test_chance_is_the_same_rule_the_dice_use(target, modifier, expected, why):
    assert chance(target, modifier) == pytest.approx(expected), why


def test_three_two_plus_rolls_are_not_three_safe_rolls():
    """The number the SOUL quotes, computed rather than asserted."""
    assert chance(2) ** 3 == pytest.approx(0.5787, abs=0.001)


# --- routes ---------------------------------------------------------------


def test_an_unmarked_player_walks_free_and_a_marked_one_pays_a_dodge():
    free = routes(board(orc(8, 8)), "h00")
    assert free["squares"][0]["chance"] == 1.0, "nobody Marking, nothing to roll"

    marked = routes(board(orc(8, 13), rat(8, 14), rat(9, 14)), "h00")
    best = marked["squares"][0]
    assert best["chance"] == pytest.approx(4 / 6, abs=0.001), "leaving a Marked square is a Dodge"


def test_the_route_multiplies_its_steps():
    """A run past the Move Allowance is Rushes, and the route is their PRODUCT.

    Nobody Marking, MA 1, walking three squares out into open field: the first is
    free, the next two are Rushes at 2+. Not "two safe steps" — 69%.
    """
    m = board(orc(8, 8, ma="1"))
    far = next(s for s in routes(m, "h00")["squares"] if (s["x"], s["y"]) == (8, 11))
    assert far["steps"] == 3
    assert far["chance"] == pytest.approx((5 / 6) * (5 / 6), abs=0.001), "two Rushes, multiplied"


def test_the_worst_route_on_a_crowded_board_is_grim_and_says_so():
    """Chained Dodges compound fast, which is the whole reason to compute rather
    than eyeball: a route can be under 5% and still look like "a few moves"."""
    m = board(orc(8, 13, ma="1"), rat(8, 14), rat(9, 14))
    worst = min(s["chance"] for s in routes(m, "h00")["squares"])
    assert worst < 0.1, f"expected a compounding disaster, got {worst}"


def test_the_safest_route_to_the_end_zone_is_named():
    """The destination that decides the game, not left to be picked out of 200."""
    r = routes(board(orc(8, 24)), "h00")
    assert r["to_end_zone"]["y"] == 26, "home attacks row 26"
    assert r["to_end_zone"]["chance"] == 1.0


def test_a_loose_ball_carries_its_pick_up_odds():
    """Walking to a ball you then fumble is how a drive ends, so the two tests are
    reported together rather than one at a time."""
    m = board(orc(8, 13), rat(2, 2))
    m.ball.carrier = ""
    m.ball.x, m.ball.y, m.ball.in_play, m.ball.in_air = 8, 15, True, False
    r = routes(m, "h00")
    ball = r["to_ball"]
    assert ball["pick_up"] == pytest.approx(4 / 6, abs=0.001), "AG 3+, unmarked"
    assert ball["chance_with_pick_up"] == pytest.approx(ball["chance"] * ball["pick_up"], abs=0.001)


def test_it_says_what_it_leaves_out():
    """An honest number names its own edges — the same reason unmodelled skills
    are reported rather than silently skipped."""
    r = routes(board(orc(8, 8)), "h00")
    assert r["unknowns"], "odds with no stated limits are the ones that mislead"
    assert any("turnover" in u.lower() for u in r["unknowns"])


def test_a_route_never_walks_through_anybody():
    m = board(orc(8, 13), rat(8, 14), rat(9, 14))
    r = routes(m, "h00")
    taken = {(rat_.x, rat_.y) for rat_ in m.players if rat_.side == "away"}
    for s in r["squares"]:
        assert (s["x"], s["y"]) not in taken


# --- the two must not drift apart -----------------------------------------


def test_every_square_the_engine_allows_is_reachable():
    """THE DRIFT GUARD. `routes` walks the board with the same rule helpers
    `move.validate` uses but its own loop, so the two could disagree — and a board
    that hides a square the engine would allow is a board that plays worse than
    the rules do.

    Containment, NOT step-for-step equality: a square next door may be reached
    more safely the long way round (see the test below), and demanding they match
    exactly asserts the algorithm is dumber than it is. That is how this test
    failed the first time it ran.
    """
    m = board(orc(8, 13), rat(8, 14), rat(9, 14))
    legal = {(s["x"], s["y"]) for s in legal_moves(m, "h00")["squares"] if s["legal"]}
    reachable = {(s["x"], s["y"]) for s in routes(m, "h00")["squares"]}
    assert legal <= reachable, f"routes cannot reach {legal - reachable}, which the engine allows"


def test_the_safest_route_is_sometimes_the_longer_one():
    """REAL BLOOD BOWL, found by the search rather than taught to it.

    Stepping straight from (8,13) into (7,13) is a Dodge at -1, because the Skaven
    on (8,14) Marks that square: 50%. Dodging out to (7,12) first — unmarked, so
    no modifier — and then walking into (7,13) from a square nobody Marks needs no
    second Dodge at all: 67%. Two steps, better odds. A coach who only ever asked
    "can I step there" would never see it.
    """
    m = board(orc(8, 13), rat(8, 14), rat(9, 14))
    into = next(s for s in routes(m, "h00")["squares"] if (s["x"], s["y"]) == (7, 13))
    assert into["steps"] == 2, "the direct step is the worse one"
    assert into["chance"] == pytest.approx(4 / 6, abs=0.001)
    assert into["path"][0] == [7, 12], "out of the tackle zone first, then in"


def test_the_situation_states_direction_rather_than_leaving_it_to_be_derived():
    """An agent playing a seat was recorded mid-turn arguing with itself about
    which way it was attacking. The engine has always known."""
    m = board(orc(8, 13), rat(8, 14))
    s = situation(m)
    assert s["scores_in"] == {"home": 26, "away": 1}
    assert s["active"] in ("home", "away")
    assert "turns_left_this_half" in s
