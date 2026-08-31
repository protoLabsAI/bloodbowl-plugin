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


def read_board():
    """The saved match as a dict, for tests that used to call `bb_game_state`.

    That tool is gone: the board rides the prompt now, and a seat that could ask
    for it called it 66 times in one turn while already holding it. Tests still
    need to look at the position, so they read the STORE — which is what the tool
    did anyway, minus a round trip.
    """

    from bloodbowl.engine.game import state_report
    from bloodbowl.store import load_match

    m = load_match()
    if m is None:
        return {"ok": False, "error": "no match in progress"}
    return {"ok": True, **state_report(m)}


def place(match, pid, x, y):
    """Put a player where the test wants them, AFTER the match starts.

    Two things this had to learn. `new_match` runs the whole start-of-drive
    sequence, so the coordinates a Scenario was built with do not survive it — a
    test that assumes they do is testing a board nobody set up.

    And `PlayerState.x` is READ-ONLY on purpose: state is derived and `apply()`
    is the only mutation, so a drill is set up by EMITTING THE EVENT rather than
    by assigning. That is the invariant that makes a saved match replay exactly,
    and a test is not exempt from it.
    """
    p = match.by_id(pid)
    # `move_to` is the mutation the fold itself performs for a `player_moved`
    # event. The EVENT is not what a drill wants: applying one also sets `acted`,
    # so the player it just positioned can no longer do anything — which is how
    # this helper failed on its second attempt.
    p.move_to(x, y)
    return p


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


# --- the mistake a real match made ----------------------------------------


def test_the_carrier_is_warned_before_it_throws_a_block():
    """FROM A LIVE MATCH, TURN ONE. A coach picked the ball up, declared a Blitz
    with the same player, rolled Player Down and ended its own turn on its first
    activation. Every block it was offered was legal and nothing said what it was
    about to risk.

    The engine still does not refuse — hitting while carrying is legal, and at the
    end of a half it can even be right. It reports the cost, the way
    `bb_pitch_review` reports rather than vetoes. Making the price visible is the
    engine's job; making the call is the coach's.
    """
    m = board(orc(8, 13), rat(8, 14))
    place(m, "h00", 8, 13)
    place(m, "a00", 8, 14)
    m.ball.carrier = "h00"
    m.ball.x, m.ball.y, m.ball.in_play, m.ball.in_air = 8, 13, True, False

    blocks = legal_moves(m, "h00")["blocks"]
    assert blocks, "the Orc is adjacent to a Skaven and may legally block"
    for b in blocks:
        assert b["carrying_the_ball"] is True
        assert "carrying the ball" in b["warning"]
        assert "Hit with somebody else" in b["warning"]


def test_a_player_without_the_ball_gets_no_such_warning():
    """The warning has to mean something. One that fires on every block is one
    nobody reads — the same reason unmodelled skills are named once each."""
    m = board(orc(8, 13), orc(2, 2), rat(8, 14))
    place(m, "h00", 8, 13)
    place(m, "h01", 2, 2)
    place(m, "a00", 8, 14)
    # The ball has to be DOWN — while it is still in the air from the kick-off
    # nothing is blockable and the test would pass for the wrong reason.
    m.ball.carrier = "h01"
    m.ball.x, m.ball.y, m.ball.in_play, m.ball.in_air = 2, 2, True, False
    blocks = legal_moves(m, "h00")["blocks"]
    assert blocks
    assert not any("warning" in b for b in blocks)


def test_the_skill_states_the_carrier_rule_as_a_rule():
    """The procedure was followed correctly and still lost the turn, because it
    never said this. A threshold would not have helped — "never" is the content.
    """
    from pathlib import Path

    body = Path(__file__).resolve().parent.parent / "skills" / "coaching-a-turn" / "SKILL.md"
    text = " ".join(body.read_text().split())
    assert "THE BALL CARRIER DOES NOT HIT ANYONE" in text
    assert "never throws a Block, never declares a Blitz, and never Fouls" in text
    assert "outranks everything below it" in text, "it has to beat the other steps, not sit among them"


# --- the parallel-call race ------------------------------------------------


def test_two_actions_at_once_do_not_lose_one(registry, tmp_path):
    """FROM A LIVE MATCH. A model batches independent tool calls in parallel —
    normal, and usually what you want. Two of them here both loaded the same
    match, each applied their action, and the second save silently discarded the
    first. The action did not fail; it VANISHED.

    The coach worked it out itself and still lost the turn to it: "I intended to
    build a cage but the cage moves (h03, h06) did not persist due to the
    parallel-call race." It then re-read the board 22 times and tried to end its
    turn 11 times, arguing with a board that disagreed with what it had just done.

    This drives the REAL tool from threads, because the bug only exists in the
    read-modify-write round trip — a test that called `game.act` directly would
    pass against the broken version.
    """
    import json as j
    import threading

    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}

    # A board with two home players who can each walk somewhere harmless.
    tools["bb_pitch_clear"].invoke({})
    for pos, x, y in (("Orc Lineman", 5, 10), ("Orc Lineman", 9, 10)):
        tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": pos, "x": x, "y": y})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 2, "y": 20})
    tools["bb_game_new"].invoke({"seed": 4, "kicking_to": "home"})
    _decline_any_pending(tools)

    results: list = []

    def move(pid, x, y):
        results.append(j.loads(tools["bb_game_act"].invoke({"action": "move", "player": pid, "x": x, "y": y})))

    a = threading.Thread(target=move, args=("h00", 5, 11))
    b = threading.Thread(target=move, args=("h01", 9, 11))
    a.start(), b.start()
    a.join(), b.join()

    state = read_board()
    where = {p["id"]: (p["x"], p["y"]) for p in state["match"]["players"]}
    moved = sum(1 for pid, sq in (("h00", (5, 11)), ("h01", (9, 11))) if where.get(pid) == sq)
    assert moved == 2, f"one of two parallel moves was lost: {where}, replies {[r.get('ok') for r in results]}"


def _decline_any_pending(tools):
    """Kick-off Events that ask a question block everything until answered."""

    for _ in range(4):
        st = read_board()
        if not st.get("waiting_on"):
            return
        tools["bb_game_choose"].invoke({"decline": True})


def test_routes_says_how_far_downfield_is_safe():
    """`to_end_zone` only exists when the End Zone is REACHABLE, which from
    fifteen rows out it never is. A coach asking "can I score?" then gets nothing
    and has to pick a square out of two hundred sorted by safety — sorted by
    exactly the wrong thing for this question.

    Observed: a carrier stayed upright for two whole turns and gained three rows,
    sideways. A drive has about six usable turns and a pitch is 26 rows; one row a
    turn never scores.
    """
    m = board(orc(2, 11))
    place(m, "h00", 2, 11)
    d = routes(m, "h00")["downfield"]

    assert d["safe"]["rows_gained"] >= 5, "MA 6 in open field should cover real ground"
    assert d["safe"]["chance"] >= 0.94
    # Home attacks row 26, so progress means y increasing.
    assert d["safe"]["y"] > 11
    # The bold line risks more for more, and must not be safer than the safe one.
    assert d["bold"]["rows_gained"] >= d["safe"]["rows_gained"]
    assert d["bold"]["chance"] <= d["safe"]["chance"]


def test_downfield_runs_the_other_way_for_the_other_side():
    """Away attacks row 1. A progress metric that assumed one direction would send
    half the players backwards, confidently."""
    m = board(orc(8, 8), rat(8, 20))
    place(m, "a00", 8, 20)
    d = routes(m, "a00")["downfield"]
    assert d["safe"]["y"] < 20, "away runs at row 1"
    assert d["safe"]["rows_gained"] > 0


def test_the_skill_tells_the_coach_to_advance_the_ball():
    """The first version of the list had score, protect, blitz, block, cage and
    screen — and no step that moved the ball. Every other step feels more urgent,
    which is exactly why it has to be numbered."""
    from pathlib import Path

    text = " ".join(
        (Path(__file__).resolve().parent.parent / "skills" / "coaching-a-turn" / "SKILL.md").read_text().split()
    )
    assert "MOVE THE BALL DOWNFIELD" in text
    assert "ends the turn closer to the line than it started" in text
    assert "never scores" in text, "say what one row a turn costs"


# --- the board in the prompt ----------------------------------------------


def test_the_board_renders_small_enough_to_send_every_call():
    """A seat read the position 9-16 times a turn, three quarters of it roster
    data that cannot change. Riding the prompt it is sent once per model call,
    so it has to be cheap — and it has to state the two facts every decision
    hangs off rather than leaving them to be derived."""
    from bloodbowl import middleware

    m = board(orc(8, 13), rat(8, 14))
    place(m, "h00", 8, 13)
    text = middleware.render(m)

    assert middleware.MARK in text, "it must be findable, so a later call can replace it"
    assert "home runs at row 26" in text and "away runs at row 1" in text
    assert "h00" in text and "a00" in text
    assert len(text) < 2000, f"{len(text)} chars is too much to send on every call"


def test_the_board_block_replaces_itself_rather_than_stacking():
    """SIXTEEN STALE BOARDS WOULD BE WORSE THAN THE READS THEY REPLACE.

    Tested through the plain `attach` rather than the middleware class, because
    `AgentMiddleware` comes from the host and a test of the class SKIPS wherever
    this suite runs — which is where a regression would actually be caught.
    """
    from bloodbowl import middleware

    real = [{"type": "text", "text": "the real system prompt"}]
    once = middleware.attach(real, middleware.MARK + "\nboard one")
    twice = middleware.attach(once, middleware.MARK + "\nboard two")

    boards = [b for b in twice if middleware.MARK in str(b.get("text", ""))]
    assert len(boards) == 1, f"{len(boards)} board blocks — they are stacking"
    assert "board two" in boards[0]["text"], "it kept the stale one"
    assert any("the real system prompt" in str(b.get("text", "")) for b in twice), "it ate the prompt"


def test_attach_handles_a_plain_string_prompt_and_refuses_anything_else():
    from bloodbowl import middleware

    out = middleware.attach("a string prompt", "BOARD")
    assert out[0]["text"] == "a string prompt" and out[-1]["text"] == "BOARD"
    assert middleware.attach(None, "BOARD") is None, "nothing safe to attach to"


def test_no_board_no_block():
    """Ordinary chat must not carry a board. The middleware is registered for the
    whole agent, not just for a seat."""
    import bloodbowl.store as store
    from bloodbowl import middleware

    mw = middleware.factory({})
    if mw is None:
        import pytest as _pytest

        _pytest.skip("no host langchain available")

    store.clear_match() if hasattr(store, "clear_match") else None
    from pathlib import Path

    p = store.match_path()
    if Path(p).exists():
        Path(p).unlink()

    class R:
        system_message = None

    assert mw._transform(R()) is not None, "no match must be a no-op, not a crash"


def test_the_board_does_not_announce_a_question_that_is_not_there():
    """`pending` can be a dict that exists and says nothing. "WAITING ON AN
    ANSWER: None" is worse than silence — it stops a coach for nothing."""
    from bloodbowl import middleware

    m = board(orc(8, 13), rat(8, 14))
    m.pending = {}
    assert "WAITING" not in middleware.render(m)
    m.pending = {"kind": "quick_snap", "question": "up to 5 players may move one square"}
    assert "WAITING ON AN ANSWER" in middleware.render(m)


def test_ending_a_turn_ends_a_charge_that_is_still_running():
    """FROM A LIVE MATCH. `charge.should_end` is only consulted inside `act`, so a
    coach that ended its turn mid-Charge — reasonable, and what the procedure tells
    it to do when out of useful moves — advanced the clock and left `match.charge`
    populated forever.

    Nothing refused and nothing logged. The cost was on the BOARD: it shows a bar
    whenever a Charge is live, so a finished one pinned "Charge! away — 5 of 5
    still to activate" above a game that had moved on two turns, and the match read
    as frozen to the person watching it.
    """
    from bloodbowl.engine import charge
    from bloodbowl.engine.game import end_turn

    m = board(orc(8, 13), rat(8, 14))
    charge.start(m, "away", ["a00"], land="home")
    assert charge.active(m), "the fixture must actually start one"

    end_turn(m, forced=True)
    assert not charge.active(m), "the Charge outlived the turn it was running in"
    assert not m.charge, "match.charge must be empty, or the board keeps its banner"


# --- the crash that killed a live turn -------------------------------------


def test_walk_takes_the_square_shape_routes_hands_back():
    """FROM A LIVE MATCH, MID-GAME. `bb_game_routes` reports squares as
    {"x":…, "y":…, "chance":…} and its suggested paths as [[x, y], …]. A coach
    copied the first into `path=`, and `walk` did `sq[0]` on a dict.

    KeyError: 0 — which was not a refusal it could read. It crashed the tool, then
    the tool-error handler, then the whole turn, and the match stopped dead with
    "unhandled exception" in the server log.

    Both shapes now work, because both are things the engine itself hands out.
    """
    from bloodbowl.engine.game import walk

    m = board(orc(8, 8))
    place(m, "h00", 8, 8)

    as_pairs = walk(m, "h00", [[8, 9], [8, 10]], cmd={"player": "h00"}, dice=None)
    assert as_pairs.get("steps_taken") == 2, as_pairs

    m2 = board(orc(8, 8))
    place(m2, "h00", 8, 8)
    as_dicts = walk(m2, "h00", [{"x": 8, "y": 9}, {"x": 8, "y": 10}], cmd={"player": "h00"}, dice=None)
    assert as_dicts.get("steps_taken") == 2, as_dicts


def test_an_unreadable_path_is_refused_in_words_not_raised():
    """A plan the engine cannot parse is the same kind of thing as an illegal
    move, and deserves the same answer. An exception here takes the turn down."""
    from bloodbowl.engine.game import walk

    m = board(orc(8, 8))
    for junk in ([{"nope": 1}], ["8,9"], [[8]], [None]):
        out = walk(m, "h00", junk, cmd={"player": "h00"}, dice=None)
        assert out["ok"] is False, junk
        assert "square" in out["error"], out


def test_one_call_plans_the_whole_team(registry):
    """A coach asking `routes` per player spends eleven calls before it has moved
    anybody. Measured on a live seat: 30 route calls and 17 odds calls in a turn
    that ran out of time and was retried six times.

    Telling it to ask less does not work — asking was the only way it had to find
    out. So one call covers the side.
    """
    import json as j

    import bloodbowl

    bloodbowl.register(registry)
    tools = {t.name: t for t in registry.tools}
    assert "bb_game_plan" in tools

    tools["bb_pitch_clear"].invoke({})
    for pos, x, y in (("Orc Lineman", 5, 10), ("Orc Blitzer", 9, 10), ("Orc Lineman", 7, 12)):
        tools["bb_pitch_place"].invoke({"side": "home", "team": "Orc", "position": pos, "x": x, "y": y})
    tools["bb_pitch_place"].invoke({"side": "away", "team": "Skaven", "position": "Skaven Clanrat", "x": 8, "y": 20})
    tools["bb_game_new"].invoke({"seed": 8, "kicking_to": "home"})
    _decline_any_pending(tools)

    plan = j.loads(tools["bb_game_plan"].invoke({}))
    assert plan["ok"], plan
    assert len(plan["players"]) >= 3, "every player who can still act gets a row"
    for row in plan["players"]:
        assert "player" in row and "at" in row
        # The point of the call: how much ground each can take, without asking again.
        assert "safe" in row or "to_end_zone" in row or row["down"] != "standing", row
    # The carrier, if any, leads — the order the procedure works in.
    assert plan["players"][0]["has_ball"] or not any(r["has_ball"] for r in plan["players"])


def test_the_skill_starts_the_turn_with_the_team_plan():
    from pathlib import Path

    text = " ".join(
        (Path(__file__).resolve().parent.parent / "skills" / "coaching-a-turn" / "SKILL.md").read_text().split()
    )
    assert "ONE `bb_game_plan` at the top of the turn" in text
    assert "only for the player you are ABOUT TO MOVE" in text


def test_the_board_tells_each_seat_which_side_it_is():
    """ "still seeing them get confused about whose turn it is."

    The board said who was TO ACT and nothing about who was READING it. A seat had
    to carry its own identity from the nudge message, and across a retry or an
    interruption it lost it and started arguing with itself. Two agent seats are
    otherwise identical — `Match.session_ids` is the only thing that tells them
    apart.
    """
    from bloodbowl import middleware
    from bloodbowl.engine.game import new_match
    from bloodbowl.pitch import Scenario

    sc = Scenario(name="t", home_team="Orc", away_team="Skaven")
    sc.players = [orc(8, 12), rat(8, 15)]
    m = new_match(
        sc,
        seed=5,
        kicking_to="home",
        controllers={"home": "agent", "away": "agent"},
        session_ids={"home": "bloodbowl:abc:home", "away": "bloodbowl:abc:away"},
    )

    # Matched on PREFIX: the nudge keys a session per turn off the per-match seat id.
    mine = middleware.render(m, "bloodbowl:abc:home:h1t1")
    assert "YOU ARE PLAYING HOME" in mine
    assert "IT IS YOUR TURN" in mine

    theirs = middleware.render(m, "bloodbowl:abc:away:h1t1")
    assert "YOU ARE PLAYING AWAY" in theirs
    assert "not yours" in theirs, "a seat must know when to stand down"

    # Ordinary chat is not a seat and must not be told it is one.
    assert "YOU ARE PLAYING" not in middleware.render(m, "")
    assert middleware.seat_of(m, "some-other-chat") == ""
