"""Engine tests: the fold, determinism, and the movement rules.

Every rule asserted here was read off the S3 source and is quoted in the test that
pins it. Several of them differ from what an older edition would say, which is the
point — those are the ones that would otherwise get "fixed" back to wrong.

Dice are scripted throughout. A rules test that rolls real dice is testing luck.
"""

from __future__ import annotations

import pytest


def _match(*players, active="home"):
    """A match with the given (side, x, y, MA, AG, skills) players, ids h00.. a00.."""
    from bloodbowl.engine.state import Match, PlayerState
    from bloodbowl.pitch import Player

    m = Match()
    for i, spec in enumerate(players):
        side, x, y = spec[0], spec[1], spec[2]
        ma = spec[3] if len(spec) > 3 else 6
        ag = spec[4] if len(spec) > 4 else "3+"
        skills = list(spec[5]) if len(spec) > 5 else []
        p = Player(side=side, x=x, y=y, position=f"{side.title()} {i}", MA=str(ma), AG=ag, AV="9+", skills=skills)
        m.players.append(PlayerState(player=p, id=f"{side[0]}{i:02d}"))
    m.clock.active = active
    return m


def _dice(script, block=None):
    from bloodbowl.engine.dice import ScriptedDice

    return ScriptedDice(script=list(script), block_script=list(block or []))


def _move(m, pid, x, y, dice):
    """resolve applies its own events (see actions.Outcome) — do not re-apply."""
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("move")["resolve"](m, {"player": pid, "x": x, "y": y}, dice)


def _block(m, pid, target, dice, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("block")["resolve"](m, {"player": pid, "target": target, **cmd}, dice)


# --- determinism and replay -----------------------------------------------


def test_the_fold_rebuilds_a_position_exactly():
    """Re-watching is fold(events) — no dice, no rules, so it cannot drift."""
    from bloodbowl.engine.state import Match, fold

    m = _match(("home", 7, 13))
    _move(m, "h00", 7, 14, _dice([]))
    _move(m, "h00", 7, 15, _dice([]))
    log = [e for e in m.events]

    rebuilt = fold(_match(("home", 7, 13)), log)
    assert (rebuilt.by_id("h00").x, rebuilt.by_id("h00").y) == (7, 15)
    assert rebuilt.by_id("h00").ma_used == 2
    assert isinstance(rebuilt, Match)


def test_a_saved_match_round_trips_through_its_log():
    """The log is the truth: from_dict recomputes the position rather than
    trusting the cached board, so the two can never disagree."""
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13))
    _move(m, "h00", 7, 14, _dice([]))
    data = m.to_dict()
    # Corrupt the cached position; the log still says where the player is.
    data["players"][0]["x"] = 1
    data["players"][0]["y"] = 1
    back = Match.from_dict(data)
    assert (back.by_id("h00").x, back.by_id("h00").y) == (7, 14)


def test_the_same_seed_and_commands_produce_the_same_match():
    from bloodbowl.engine.dice import SeededDice

    results = []
    for _ in range(2):
        m = _match(("home", 7, 13, 1), ("away", 8, 14))
        dice = SeededDice(seed=99)
        _move(m, "h00", 7, 14, dice)
        _move(m, "h00", 7, 15, dice)  # same stream, as a real match would use
        results.append([e.to_dict() for e in m.events])
    assert results[0] == results[1]


def test_replay_dice_refuse_to_invent_a_roll():
    """If today's engine wants more dice than the recording holds, that is a rules
    change altering a real game — it must be loud."""
    from bloodbowl.engine.dice import ReplayDice, ReplayDivergence

    d = ReplayDice(recorded=[])
    with pytest.raises(ReplayDivergence):
        d.d6()


def test_scripted_dice_refuse_to_fall_back_to_random():
    with pytest.raises(AssertionError):
        _dice([]).d6()


# --- tackle zones and Marking ---------------------------------------------


def test_only_standing_players_mark():
    """S3: a Prone or Stunned player has no Tackle Zone, so you may walk away
    from someone you just knocked down without Dodging."""
    from bloodbowl.engine.rules import is_marked

    m = _match(("home", 7, 13), ("away", 7, 14))
    assert is_marked(m, m.by_id("h00")) is True
    m.by_id("a01").down = "prone"
    assert is_marked(m, m.by_id("h00")) is False


def test_a_distracted_player_does_not_mark():
    """S3 adds Distracted: Standing, but with no Tackle Zone."""
    from bloodbowl.engine.rules import is_marked

    m = _match(("home", 7, 13), ("away", 7, 14))
    m.by_id("a01").distracted = True
    assert is_marked(m, m.by_id("h00")) is False


def test_the_dodge_modifier_counts_the_destination_not_the_origin():
    """THE rule most likely to be modelled backwards. S3: "applying a -1 modifier
    for each opposition player that is Marking the square they are moving into."
    The square being LEFT decides only whether a Dodge is needed at all."""
    from bloodbowl.engine.rules import dodge_modifier

    # Three opponents Mark where the runner stands. None Marks (7,12), the square
    # being moved into — note they must be two rows behind it, since a diagonal
    # neighbour of the origin is usually also a neighbour of the destination.
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 6, 14), ("away", 8, 14))
    assert dodge_modifier(m, m.by_id("h00"), 7, 12) == 0, "leaving three tackle zones is still an unmodified roll"

    # One opponent Marks the destination.
    m2 = _match(("home", 7, 13), ("away", 7, 11))
    assert dodge_modifier(m2, m2.by_id("h00"), 7, 12) == -1


# --- movement --------------------------------------------------------------


def test_an_unmarked_move_rolls_nothing():
    m = _match(("home", 7, 13))
    out = _move(m, "h00", 7, 14, _dice([]))  # empty script: any roll would raise
    assert out.ok and not out.turnover
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14)


def test_leaving_a_marked_square_requires_a_dodge():
    m = _match(("home", 7, 13), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([4]))  # AG 3+, unmodified, 4 passes
    assert out.ok
    assert any(r.kind == "Dodge" for e in out.events for r in e.rolls)


def test_a_failed_dodge_still_moves_the_player_then_falls_over():
    """S3: "The player is moved into the square they attempted to Dodge into and
    then Falls Over." Where they land matters — the ball scatters from there."""
    m = _match(("home", 7, 13), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([2, 1, 1]))  # dodge fails, then armour holds
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (6, 12), "a failed Dodge does not leave the player where they started"
    assert p.down == "prone"


def test_a_natural_one_fails_a_dodge_however_good_the_odds():
    m = _match(("home", 7, 13, 6, "2+"), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([1, 1, 1]))
    assert not out.ok and out.turnover


def test_rushing_starts_when_the_move_allowance_runs_out():
    """S3 calls it Rushing, it is 2+, and at most twice per activation."""
    m = _match(("home", 7, 13, 1))
    assert _move(m, "h00", 7, 14, _dice([])).ok  # the one square of MA
    out = _move(m, "h00", 7, 15, _dice([3]))  # first Rush
    assert out.ok
    assert any(r.kind == "Rush" and r.target == 2 for e in out.events for r in e.rolls)


def test_a_failed_rush_falls_over_in_the_target_square_and_is_a_turnover():
    m = _match(("home", 7, 13, 1))
    _move(m, "h00", 7, 14, _dice([]))
    out = _move(m, "h00", 7, 15, _dice([1, 1, 1]))  # rush fails, then armour holds
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (7, 15) and p.down == "prone"


def test_a_third_rush_is_refused_before_any_dice_are_rolled():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 1))
    _move(m, "h00", 7, 14, _dice([]))
    _move(m, "h00", 7, 15, _dice([5]))
    _move(m, "h00", 7, 16, _dice([5]))
    legal = actions.get("move")["validate"](m, {"player": "h00", "x": 7, "y": 17})
    assert legal.ok is False and "Rush" in legal.reason


def test_rush_is_rolled_before_dodge_when_a_square_needs_both():
    """S3: "the roll for attempt to Rush will always come first". The order is
    observable: a failed Rush means the Dodge is never rolled at all."""
    m = _match(("home", 7, 13, 1), ("away", 7, 14))
    _move(m, "h00", 6, 12, _dice([5]))  # spend the single square, dodging away
    m.by_id("h00").down = "standing"
    out = _move(m, "h00", 6, 11, _dice([1, 1, 1]))  # Rush fails, then the armour roll
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Dodge" not in kinds, f"a failed Rush must end it before the Dodge: {kinds}"
    assert kinds[0] == "Rush"


# --- standing up -----------------------------------------------------------


def test_standing_up_costs_three_squares():
    m = _match(("home", 7, 13, 6))
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 14, _dice([]))
    assert out.ok
    assert m.by_id("h00").down == "standing"
    assert m.by_id("h00").ma_used == 4, "3 to stand up plus 1 to move"


def test_a_low_movement_player_rolls_to_stand_and_may_fail():
    """S3: MA 2 or less rolls a D6 — 4+ stands using the full Move Allowance,
    1-3 remains Prone with the activation immediately over."""
    m = _match(("home", 7, 13, 2))
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 14, _dice([3]))
    assert not out.ok
    assert m.by_id("h00").down == "prone"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "a failed stand-up does not move the player"


def test_jump_up_stands_for_free():
    """The Skill registry doing its job: one registration, no branch in move.py."""
    m = _match(("home", 7, 13, 6, "3+", ["Jump Up"]))
    m.by_id("h00").down = "prone"
    out = _move(m, "h00", 7, 14, _dice([]))
    assert out.ok
    assert m.by_id("h00").ma_used == 1, "Jump Up costs nothing to stand"


# --- skills ---------------------------------------------------------------


def test_the_dodge_skill_rerolls_a_failed_dodge_once_per_turn():
    m = _match(("home", 7, 13, 6, "3+", ["Dodge"]), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([2, 5]))  # fail, then re-roll and pass
    assert out.ok
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds == ["Dodge", "Dodge (re-roll)"]
    assert m.by_id("h00").dodge_reroll_used is True


def test_prehensile_tail_is_an_opponents_skill_that_modifies_our_roll():
    """The hook is asked of the players Marking the destination, not of the
    player rolling — which is why it is a separate hook."""
    m = _match(("home", 7, 13), ("away", 7, 14), ("away", 7, 11, 6, "3+", ["Prehensile Tail"]))
    out = _move(m, "h00", 7, 12, _dice([3, 1, 1]))  # AG 3+, -1 marker, -1 tail = fails
    assert not out.ok
    dodge = next(r for e in out.events for r in e.rolls if r.kind == "Dodge")
    assert dodge.modifier == -2


def test_unmodelled_skills_are_reported_rather_than_ignored():
    """A Troll's Always Hungry is not implemented. The engine says so instead of
    quietly playing as though the player did not have it."""
    m = _match(("home", 7, 13, 6, "3+", ["Always Hungry", "Really Stupid", "Jump Up", "Mighty Blow"]))
    out = _move(m, "h00", 7, 14, _dice([]))
    assert "Always Hungry" in out.unmodelled
    assert "Really Stupid" in out.unmodelled
    # Both of these ARE modelled, and the list must shrink as skills land — this
    # test caught Mighty Blow moving from unmodelled to modelled when Blocking
    # was added, which is exactly the drift it is here to notice.
    assert "Jump Up" not in out.unmodelled
    assert "Mighty Blow" not in out.unmodelled


# --- strict in play --------------------------------------------------------


def test_the_engine_refuses_an_illegal_move_rather_than_reporting_it():
    """The setup board reports and never blocks. A match is the opposite half of
    that choice: it refuses, so a coach cannot talk the engine into a bad move."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13), ("away", 7, 14))
    for cmd, expect in (
        ({"player": "h00", "x": 7, "y": 14}, "occupied"),
        ({"player": "h00", "x": 7, "y": 20}, "one square"),
        ({"player": "a01", "x": 7, "y": 15}, "turn"),
        ({"player": "nope", "x": 7, "y": 14}, "no player"),
    ):
        legal = actions.get("move")["validate"](m, cmd)
        assert legal.ok is False and expect in legal.reason, f"{cmd} -> {legal.reason}"


def test_validate_is_free_of_side_effects_so_a_coach_can_ask_about_every_square():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13), ("away", 7, 14))
    before = m.to_dict()
    for x in range(1, 16):
        actions.get("move")["validate"](m, {"player": "h00", "x": x, "y": 12})
    assert m.to_dict() == before, "asking what is legal must not change the game"


# --- the game loop through the tools --------------------------------------


def _tools(registry):
    import bloodbowl

    bloodbowl.register(registry)
    return {t.name: t for t in registry.tools}


def _setup_board(home="Orc", away="Skaven"):
    """A small legal-ish board saved where the tools will find it."""
    import json as _json

    from bloodbowl.pitch import Player, Scenario
    from bloodbowl.store import save

    sc = Scenario(name="test", home_team=home, away_team=away)
    sc.players = [
        Player(side="home", x=7, y=13, position="Orc Lineman", team=home, MA="5", AG="3+", AV="10+"),
        Player(side="away", x=7, y=14, position="Skaven Clanrat", team=away, MA="7", AG="3+", AV="8+"),
    ]
    save(sc)
    return _json


def test_a_match_starts_from_the_board_without_consuming_it(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    out = j.loads(t["bb_game_new"].invoke({"seed": 7}))
    assert out["ok"] and len(out["match"]["players"]) == 2
    assert out["match"]["clock"]["half"] == 1

    from bloodbowl.store import load

    assert len(load().players) == 2, "starting a match must not clear the practice board"


def test_an_empty_board_cannot_start_a_match(registry):
    import json as j

    from bloodbowl.pitch import Scenario
    from bloodbowl.store import save

    save(Scenario())
    t = _tools(registry)
    out = j.loads(t["bb_game_new"].invoke({}))
    assert out["ok"] is False and "empty" in out["error"]


def test_legal_reports_every_neighbour_without_changing_the_game(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 3})
    before = j.loads(t["bb_game_state"].invoke({}))
    out = j.loads(t["bb_game_legal"].invoke({"player": "h00"}))
    assert out["ok"] and len(out["squares"]) == 8
    assert any(s["legal"] and s.get("dodge") for s in out["squares"]), "this player is Marked, so moves need a Dodge"
    after = j.loads(t["bb_game_state"].invoke({}))
    assert before == after, "asking what is legal must not advance the game"


def test_acting_is_refused_when_illegal_and_says_why(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 3})
    out = j.loads(t["bb_game_act"].invoke({"action": "move", "player": "a00", "x": 7, "y": 15}))
    assert out["ok"] is False and "turn" in out["text"]


def test_the_log_carries_the_rolls_so_the_coach_quotes_rather_than_guesses(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 11})
    t["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 6, "y": 12})
    log = j.loads(t["bb_game_log"].invoke({"last": 10}))
    assert log["ok"]
    rolled = [line for entry in log["log"] for line in entry["rolls"]]
    assert any("Dodge" in r and "needed" in r for r in rolled), f"no dodge in the log: {rolled}"


def test_the_dice_stream_advances_past_the_rolls_already_made():
    """A match is reloaded from disk between tool calls, so its dice source is
    rebuilt from the seed every time. Without advancing past the rolls already in
    the log, every action would roll the same number for the whole game — the seed
    would produce a stuck match rather than a reproducible one.
    """
    from bloodbowl.engine.dice import SeededDice
    from bloodbowl.engine.game import dice_for
    from bloodbowl.engine.state import Match

    expected = [SeededDice(seed=5).d6() for _ in range(1)]
    reference = SeededDice(seed=5)
    stream = [reference.d6() for _ in range(6)]

    m = Match(seed=5)
    assert dice_for(m).d6() == stream[0], "a fresh match starts at the head of the stream"

    # Fake three rolls into the log and the next draw must be the fourth value.
    from bloodbowl.engine.dice import Roll
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="note", rolls=[Roll(kind="d6", dice=[1]) for _ in range(3)]))
    assert dice_for(m).d6() == stream[3], "the stream must skip the rolls already recorded"
    assert expected  # guard against the reference loop being optimised away


def test_successive_actions_do_not_repeat_the_same_roll(registry):
    """The same property, end to end through the tools."""
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 5})
    # A Rush every step: MA 5 is spent, then each further square rolls.
    for step in range(6):
        t["bb_game_act"].invoke({"action": "move", "player": "h00", "x": 7 if step % 2 else 6, "y": 12 - step // 2})
    log = j.loads(t["bb_game_log"].invoke({"last": 200}))
    rolls = [line for e in log["log"] for line in e["rolls"]]
    assert len(rolls) >= 2, f"expected several rolls, got {rolls}"
    assert len({r.split("rolled ")[-1] for r in rolls}) > 1, f"every action rolled the same: {rolls}"


def test_abandoning_a_match_leaves_the_board(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 1})
    assert j.loads(t["bb_game_abandon"].invoke({}))["discarded"] is True
    assert j.loads(t["bb_game_state"].invoke({}))["ok"] is False

    from bloodbowl.store import load

    assert len(load().players) == 2


# --- blocking --------------------------------------------------------------
#
# Every rule here was read off the S3 source and is quoted in the test. Block
# dice are scripted, so a "1 in 6" never decides whether a rule works.


def _st(side, x, y, st, skills=(), ma=6, av="9+"):
    return (side, x, y, ma, "3+", list(skills), st, av)


def _match_st(*specs, active="home"):
    """Like _match but with an explicit ST and AV per player."""
    from bloodbowl.engine.state import Match, PlayerState
    from bloodbowl.pitch import Player

    m = Match()
    counts = {"home": 0, "away": 0}
    for spec in specs:
        side, x, y, ma, ag, skills, st, av = spec
        i = counts[side]
        counts[side] += 1
        p = Player(
            side=side,
            x=x,
            y=y,
            position=f"{side.title()} {i}",
            MA=str(ma),
            ST=str(st),
            AG=ag,
            PA="4+",
            AV=av,
            skills=list(skills),
        )
        m.players.append(PlayerState(player=p, id=f"{side[0]}{i:02d}"))
    m.clock.active = active
    return m


def _bdice(faces, script=()):
    from bloodbowl.engine.dice import ScriptedDice

    return ScriptedDice(script=list(script), block_script=[list(faces)])


# --- dice count and who chooses -------------------------------------------


def test_equal_strength_rolls_one_die():
    from bloodbowl.engine.rules import block_dice

    assert block_dice(3, 3) == (1, "attacker")


def test_higher_strength_rolls_two_and_the_stronger_coach_chooses():
    from bloodbowl.engine.rules import block_dice

    assert block_dice(4, 3) == (2, "attacker")
    assert block_dice(3, 4) == (2, "defender"), "blocking someone stronger hands THEM the pick"


def test_over_double_rolls_three_and_double_alone_does_not():
    """S3: three dice when one player has OVER DOUBLE the other's Strength.
    'Over' is strictly greater, so ST 4 into ST 2 is two dice, not three."""
    from bloodbowl.engine.rules import block_dice

    assert block_dice(4, 2) == (2, "attacker"), "exactly double is not over double"
    assert block_dice(5, 2) == (3, "attacker")
    assert block_dice(2, 5) == (3, "defender")


# --- assists ---------------------------------------------------------------


def test_an_assist_needs_a_team_mate_marking_the_target():
    from bloodbowl.engine.rules import assist_count

    m = _match_st(_st("home", 7, 13, 3), _st("home", 8, 14, 3), _st("away", 7, 14, 3))
    assert assist_count(m, "home", m.by_id("a00")) == 2, "both home players Mark the target"


def test_an_assister_marked_by_another_opponent_does_not_assist():
    """S3: "...and is not Marked by another opposing player." ANOTHER — the player
    being blocked does not cancel the assists against themselves."""
    from bloodbowl.engine.rules import assist_count

    # h01 Marks the target but is itself Marked by a second away player.
    m = _match_st(
        _st("home", 7, 13, 3),
        _st("home", 8, 14, 3),
        _st("away", 7, 14, 3),
        _st("away", 9, 14, 3),
    )
    assert assist_count(m, "home", m.by_id("a00")) == 1, "h01 is Marked by a01 and cannot assist"


def test_the_target_of_the_block_never_cancels_its_own_assists():
    m = _match_st(_st("home", 7, 13, 3), _st("home", 8, 14, 3), _st("away", 7, 14, 3))
    from bloodbowl.engine.rules import assist_count

    # a00 Marks both home players, but it is the one being blocked.
    assert assist_count(m, "home", m.by_id("a00")) == 2


def test_a_prone_team_mate_cannot_assist():
    from bloodbowl.engine.rules import assist_count

    m = _match_st(_st("home", 7, 13, 3), _st("home", 8, 14, 3), _st("away", 7, 14, 3))
    m.by_id("h01").down = "prone"
    assert assist_count(m, "home", m.by_id("a00")) == 1


def test_guard_assists_even_while_marked():
    """S3: Guard assists "regardless of how many opposition players are Marking
    this player"."""
    from bloodbowl.engine.rules import assist_count

    m = _match_st(
        _st("home", 7, 13, 3),
        _st("home", 8, 14, 3, skills=["Guard"]),
        _st("away", 7, 14, 3),
        _st("away", 9, 14, 3),
    )
    assert assist_count(m, "home", m.by_id("a00")) == 2, "Guard ignores being Marked"


# --- push geometry ---------------------------------------------------------


def test_an_orthogonal_push_offers_three_squares_behind_the_target():
    from bloodbowl.engine.rules import push_squares

    assert sorted(push_squares(7, 13, 7, 14)) == [(6, 15), (7, 15), (8, 15)]


def test_a_diagonal_push_offers_three_squares_not_five():
    """THE geometry trap. Read literally, "an adjacent square that is not adjacent
    to the blocker" yields FIVE squares on a diagonal; the diagrams show three.
    The arc is the push direction plus its two neighbours. Both readings agree on
    an orthogonal block, which is why the wrong one survives casual testing."""
    from bloodbowl.engine.rules import push_squares

    got = sorted(push_squares(7, 13, 8, 14))
    assert len(got) == 3, got
    assert got == [(8, 15), (9, 14), (9, 15)]


# --- block results ---------------------------------------------------------


def test_push_back_moves_the_target_and_the_blocker_may_follow_up():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    out = _block(m, "h00", "a00", _bdice(["push_back"]))
    assert out.ok and not out.turnover
    assert (m.by_id("a00").x, m.by_id("a00").y) == (7, 15), "straight back is the default push"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 14), "follow-up takes the vacated square"


def test_follow_up_can_be_declined():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    _block(m, "h00", "a00", _bdice(["push_back"]), follow_up=False)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13)


def test_pow_pushes_then_knocks_down_in_the_square_they_land_in():
    """S3: "Apply the Push Back result to the target player. The target player is
    then Knocked Down in the square they are now in.\""""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="4+"))
    out = _block(m, "h00", "a00", _bdice(["pow"], script=[6, 6, 1, 1]))
    t = m.by_id("a00")
    assert (t.x, t.y) == (7, 15), "the push happens before the knockdown"
    assert t.down in ("prone", "stunned")
    assert out.ok and not out.turnover, "knocking the OPPONENT down is not a turnover"


def test_player_down_knocks_the_blocker_over_and_is_a_turnover():
    """A Block can go wrong. PLAYER DOWN knocks down the player who threw it."""
    m = _match_st(_st("home", 7, 13, 3, av="4+"), _st("away", 7, 14, 3))
    out = _block(m, "h00", "a00", _bdice(["player_down"], script=[6, 6, 1, 1]))
    assert out.turnover and not out.ok
    assert m.by_id("h00").down in ("prone", "stunned")
    assert m.by_id("a00").down == "standing"


def test_both_down_puts_both_players_on_the_floor():
    m = _match_st(_st("home", 7, 13, 3, av="12+"), _st("away", 7, 14, 3, av="12+"))
    out = _block(m, "h00", "a00", _bdice(["both_down"], script=[1, 1, 1, 1]))
    assert m.by_id("h00").down == "prone" and m.by_id("a00").down == "prone"
    assert out.turnover, "the active team's player went down"


def test_the_block_skill_keeps_its_owner_up_on_both_down():
    """S3: "may choose not to be Knocked Down when a Both Down result is applied
    during a Block Action that they are part of.\""""
    m = _match_st(
        _st("home", 7, 13, 3, skills=["Block"], av="12+"),
        _st("away", 7, 14, 3, av="12+"),
    )
    out = _block(m, "h00", "a00", _bdice(["both_down"], script=[1, 1]))
    assert m.by_id("h00").down == "standing", "Block keeps the blocker up"
    assert m.by_id("a00").down == "prone"
    assert not out.turnover, "no turnover when the active player stayed on their feet"


def test_stumble_is_only_a_push_when_the_target_has_dodge():
    """S3: "If the target player has the Dodge skill, this becomes Push Back.
    Otherwise, this becomes POW.\""""
    dodgy = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, skills=["Dodge"]))
    _block(dodgy, "h00", "a00", _bdice(["stumble"]))
    assert dodgy.by_id("a00").down == "standing", "Dodge turns Stumble into a plain push"

    plain = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="4+"))
    _block(plain, "h00", "a00", _bdice(["stumble"], script=[6, 6, 1, 1]))
    assert plain.by_id("a00").down != "standing", "without Dodge, Stumble is a POW"


def test_the_defender_chooses_when_they_are_stronger():
    """Two dice into a stronger player: THEY pick, so the engine must apply the
    worst result for the attacker rather than the first one rolled."""
    m = _match_st(_st("home", 7, 13, 3, av="4+"), _st("away", 7, 14, 5))
    out = _block(m, "h00", "a00", _bdice(["pow", "player_down"], script=[6, 6, 1, 1]))
    assert out.turnover, "the defender picks Player Down"
    assert m.by_id("h00").down != "standing"


# --- armour and injury -----------------------------------------------------


def test_armour_that_holds_leaves_the_player_merely_prone():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="11+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[1, 1]))
    t = m.by_id("a00")
    assert t.down == "prone" and t.place == "pitch"


def test_a_broken_armour_rolls_for_injury_and_can_stun():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 2, 2]))  # armour 6 vs 3+, injury 4
    assert m.by_id("a00").down == "stunned"


def test_a_high_injury_roll_removes_the_player_from_the_pitch():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 6, 6]))  # injury 12 = Casualty
    t = m.by_id("a00")
    assert t.place == "casualty"
    assert m.at(t.x, t.y) is None, "a player in the box must stop occupying their square"


def test_a_knocked_out_player_goes_to_the_ko_box():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 4, 5]))  # injury 9 = KO
    assert m.by_id("a00").place == "knocked_out"


def test_thick_skull_turns_an_eight_into_a_stunned():
    """S3: "they will only be Knocked-out on the roll of a 9; a roll of an 8 will
    be treated as a Stunned result.\""""
    plain = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+"))
    _block(plain, "h00", "a00", _bdice(["pow"], script=[3, 3, 4, 4]))  # injury 8
    assert plain.by_id("a00").place == "knocked_out"

    tough = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="3+", skills=["Thick Skull"]))
    _block(tough, "h00", "a00", _bdice(["pow"], script=[3, 3, 4, 4]))
    assert tough.by_id("a00").place == "pitch"
    assert tough.by_id("a00").down == "stunned"


def test_mighty_blow_is_spent_on_the_armour_roll_only_when_it_breaks_it():
    """S3 lets the +1 go to EITHER roll, after the roll. Spending it on armour
    that already broke would waste it; spending it on armour that still fails
    would waste it too."""
    m = _match_st(_st("home", 7, 13, 3, skills=["Mighty Blow"]), _st("away", 7, 14, 3, av="7+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 1, 1]))  # armour 6, +1 = 7 breaks
    rolls = [r for e in m.events for r in e.rolls]
    armour = next(r for r in rolls if r.kind == "Armour")
    assert armour.passed and armour.modifier == 1, "the +1 rescued a failed armour roll"


def test_the_armour_roll_is_two_dice_not_an_agility_test():
    """A natural 1 does not auto-fail an Armour Roll, and a natural 6 does not
    auto-break it — those belong to single-die Agility Tests."""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="4+"))
    _block(m, "h00", "a00", _bdice(["pow"], script=[1, 6, 1, 1]))  # 1+6 = 7 >= 4+
    armour = next(r for e in m.events for r in e.rolls if r.kind == "Armour")
    assert armour.dice == [1, 6] and armour.total == 7 and armour.passed


# --- pushes into other players and off the pitch ---------------------------


def test_a_free_square_is_taken_before_any_chain_push():
    """A Chain Push happens only when there is NO unoccupied square. With a flank
    free the target simply goes there and nobody else moves."""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3), _st("away", 7, 15, 3))
    _block(m, "h00", "a00", _bdice(["push_back"]))
    assert (m.by_id("a00").x, m.by_id("a00").y) in ((8, 15), (6, 15))
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 15), "the occupant did not move"


def _boxed_in():
    """All three push squares behind the target occupied — the only way to chain."""
    return _match_st(
        _st("home", 7, 13, 3),
        _st("away", 7, 14, 3),
        _st("away", 7, 15, 3),
        _st("away", 6, 15, 3),
        _st("away", 8, 15, 3),
    )


def test_a_chain_push_moves_the_player_behind():
    """S3: the occupant is Pushed Back "as if they had been Pushed Back by the
    player who is now occupying their square"."""
    m = _boxed_in()
    _block(m, "h00", "a00", _bdice(["push_back"]))
    assert (m.by_id("a00").x, m.by_id("a00").y) == (7, 15), "the target took the occupied square"
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 16), "and its occupant was pushed on"


def test_a_prone_player_can_still_be_chain_pushed():
    """S3 says so explicitly — being on the floor is no protection from a shove."""
    m = _boxed_in()
    m.by_id("a01").down = "prone"
    _block(m, "h00", "a00", _bdice(["push_back"]))
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 16)
    assert m.by_id("a01").down == "prone", "a chain push does not stand anyone up"


def test_a_push_off_the_sideline_sends_the_player_into_the_crowd():
    """Pushed into the Crowd when there is no unoccupied square on the pitch."""
    m = _match_st(_st("home", 2, 13, 3), _st("away", 1, 13, 3, av="3+"))
    _block(m, "h00", "a00", _bdice(["push_back"], script=[3, 3, 2, 2]))
    t = m.by_id("a00")
    assert t.place != "pitch" or t.down != "standing"
    assert any(e.kind == "player_left_pitch" for e in m.events), "no crowd event recorded"


def test_neither_participant_counts_as_an_assist():
    """Found by distrusting a screenshot: three different targets all reported
    "ST 5 v 4", which is too tidy for three players with different neighbours.

    The blocker was assisting its own block and the target was assisting its own
    defence. Both inflate by one and both produce a plausible number, so nothing
    looks wrong — which is exactly why it survived a passing test suite."""
    from bloodbowl.engine.rules import assist_count

    # A lone blocker against a lone target: neither may assist, so both are zero.
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    blocker, target = m.by_id("h00"), m.by_id("a00")
    assert assist_count(m, "home", target, exclude={blocker.id}) == 0, "the blocker is not its own assist"
    assert assist_count(m, "away", blocker, exclude={target.id}) == 0, "the target cannot assist itself"


def test_a_lone_block_is_one_die_not_two():
    """The end-to-end shape of the same bug: with both participants counting
    themselves, an even fight came out as ST 4 v 4 — still equal, still one die —
    but any asymmetry in the surrounding players turned into a phantom modifier."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    d = actions.get("block")["validate"](m, {"player": "h00", "target": "a00"}).detail
    assert (d["attacker_strength"], d["defender_strength"]) == (3, 3)
    assert d["offensive_assists"] == 0 and d["defensive_assists"] == 0
    assert d["dice"] == 1


def test_a_team_mate_marked_by_the_blocker_alone_still_assists():
    """ "not Marked by ANOTHER opposing player" — the blocker does not cancel the
    assists of the defenders standing next to it."""
    from bloodbowl.engine.rules import assist_count

    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3), _st("away", 6, 13, 3))
    blocker, target = m.by_id("h00"), m.by_id("a00")
    # a01 Marks the blocker and is Marked only BY the blocker, so it assists.
    assert assist_count(m, "away", blocker, exclude={target.id}) == 1


# --- the ball --------------------------------------------------------------


def _with_ball(m, x, y, carrier=""):
    from bloodbowl.engine.state import Ball

    m.ball = Ball(x=x, y=y, carrier=carrier, in_play=True)
    return m


def test_a_bounce_is_one_square_in_a_d8_direction():
    """S3: "When the rules tell you to Bounce the ball, it will Scatter (1) from
    its current location.\""""
    from bloodbowl.engine.ball import DIRECTIONS, bounce

    m = _with_ball(_match(("home", 1, 1)), 7, 13)
    bounce(m, _dice([5]))
    dx, dy = DIRECTIONS[5]
    assert (m.ball.x, m.ball.y) == (7 + dx, 13 + dy)


def test_the_direction_table_is_a_bijection_onto_the_eight_neighbours():
    """The template is a diagram, so the ROTATION is conventional — but every
    direction must be reachable exactly once or the bounce is biased."""
    from bloodbowl.engine.ball import DIRECTIONS

    assert sorted(DIRECTIONS) == list(range(1, 9))
    assert len(set(DIRECTIONS.values())) == 8
    assert (0, 0) not in DIRECTIONS.values()


def test_moving_onto_a_loose_ball_attempts_a_pick_up():
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    out = _move(m, "h00", 7, 14, _dice([4]))  # AG 3+, unmarked
    assert out.ok
    assert m.ball.carrier == "h00"


def test_a_failed_pick_up_is_a_turnover_and_the_ball_bounces():
    """S3: "If the test is failed, the player fails to pick up the ball and a
    Turnover is caused - the ball will then Bounce from the player's square.\""""
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    out = _move(m, "h00", 7, 14, _dice([1, 5]))  # pick-up fails, then the bounce
    assert out.turnover and not out.ok
    assert m.ball.carrier == ""
    assert (m.ball.x, m.ball.y) != (7, 14), "the ball must have bounced away"


def test_the_pick_up_is_modified_by_each_marking_opponent():
    """S3: "Apply a -1 modifier to this roll for each opposition player that is
    Marking the player attempting to pick up the ball.\""""
    m = _with_ball(_match(("home", 7, 13), ("away", 6, 15), ("away", 8, 15)), 7, 14)
    _move(m, "h00", 7, 14, _dice([4, 5]))  # 4 would pass unmodified; -2 fails it
    pick = next(r for e in m.events for r in e.rolls if r.kind == "Pick up")
    assert pick.modifier == -2 and not pick.passed


def test_a_player_pushed_onto_the_ball_does_not_pick_it_up_and_it_bounces():
    """S3: "If a player is ever involuntarily moved into a square containing the
    ball... they may not attempt to pick it up and it will Bounce; however, no
    Turnover will be caused." A push is not an activation."""
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3))
    _with_ball(m, 7, 15)
    out = _block(m, "h00", "a00", _bdice(["push_back"], script=[5]))
    a = m.by_id("a00")
    assert (a.x, a.y) == (7, 15), "pushed onto the ball's square"
    assert m.ball.carrier == "", "no pick-up attempt from a push"
    assert not out.turnover, "and no turnover either"


def test_a_knocked_down_carrier_drops_the_ball():
    m = _match_st(_st("home", 7, 13, 3), _st("away", 7, 14, 3, av="12+"))
    _with_ball(m, 7, 14, carrier="a00")
    _block(m, "h00", "a00", _bdice(["pow"], script=[5, 1, 1]))
    assert m.ball.carrier == "", "going down means losing the ball"
    assert m.ball.in_play


def test_a_failed_dodge_also_drops_the_ball():
    """Every route to the floor loses the ball, not just a Block."""
    m = _match(("home", 7, 13), ("away", 7, 14))
    _with_ball(m, 7, 13, carrier="h00")
    _move(m, "h00", 6, 12, _dice([1, 1, 1, 5]))  # dodge fails, armour holds, bounce
    assert m.ball.carrier == ""


# --- catching --------------------------------------------------------------


def test_a_hand_off_needs_a_catch_and_can_be_dropped():
    m = _match(("home", 7, 13), ("home", 7, 14))
    _with_ball(m, 7, 13, carrier="h00")
    from bloodbowl.engine import actions

    actions.load_all()
    out = actions.get("handoff")["resolve"](m, {"player": "h00", "target": "h01"}, _dice([5]))
    assert out.ok and m.ball.carrier == "h01"

    m2 = _match(("home", 7, 13), ("home", 7, 14))
    _with_ball(m2, 7, 13, carrier="h00")
    out2 = actions.get("handoff")["resolve"](m2, {"player": "h00", "target": "h01"}, _dice([1, 5]))
    assert not out2.ok and out2.turnover, "a dropped hand-off is a turnover"
    assert m2.ball.carrier == ""


def test_a_prone_player_automatically_fails_to_catch():
    """S3: "If a Prone or Stunned player, or a player that is Distracted, is
    required to Catch a ball, they will automatically fail.\""""
    from bloodbowl.engine.ball import catch

    m = _with_ball(_match(("home", 7, 13)), 7, 13)
    m.by_id("h00").down = "prone"
    catch(m, m.by_id("h00"), _dice([5]))  # only the bounce die is needed
    assert m.ball.carrier == ""


def test_a_catch_is_modified_by_each_marking_opponent():
    from bloodbowl.engine.ball import catch

    m = _with_ball(_match(("home", 7, 13), ("away", 7, 14), ("away", 6, 14)), 7, 13)
    catch(m, m.by_id("h00"), _dice([4, 5]))
    r = next(r for e in m.events for r in e.rolls if r.kind == "Catch")
    assert r.modifier == -2


# --- Secure the Ball (new in S3) -------------------------------------------


def test_secure_the_ball_is_a_flat_two_up_when_nobody_is_near():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13, 6, "6+")), 7, 14)  # dreadful AG, still 2+
    out = actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([2]))
    assert out.ok and m.ball.carrier == "h00"
    r = next(r for e in m.events for r in e.rolls if r.kind == "Secure the Ball")
    assert r.target == 2 and r.modifier == 0, "Tackle Zones cannot reach a secured ball"


def test_secure_the_ball_is_refused_with_an_opponent_within_two_of_the_ball():
    """The clearance is measured from the BALL, not from the player."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13), ("away", 7, 16)), 7, 14)
    legal = actions.get("secure")["validate"](m, {"player": "h00"})
    assert legal.ok is False and "within 2" in legal.reason


def test_secure_the_ball_ends_the_activation():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([5]))
    assert m.by_id("h00").acted is True


def test_a_failed_secure_is_still_a_turnover():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _with_ball(_match(("home", 7, 13)), 7, 14)
    out = actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([1, 5]))
    assert out.turnover and m.ball.carrier == ""


# --- scoring ---------------------------------------------------------------


def test_carrying_the_ball_into_the_opposing_end_zone_scores():
    from bloodbowl.pitch import LENGTH

    m = _match(("home", 7, LENGTH - 1))
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    out = _move(m, "h00", 7, LENGTH, _dice([]))
    assert out.ok
    assert m.score["home"] == 1
    assert any(e.kind == "touchdown" for e in m.events)


def test_a_player_pushed_into_the_end_zone_with_the_ball_scores():
    """S3 allows a Touchdown from "a player holding the ball being Pushed or Chain
    Pushed into the opposition End Zone" — it can happen on the OPPONENT's turn."""
    from bloodbowl.pitch import LENGTH

    m = _match_st(_st("away", 7, LENGTH - 2, 3), _st("home", 7, LENGTH - 1, 3), active="away")
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    _block(m, "a00", "h00", _bdice(["push_back"]))
    assert m.score["home"] == 1, "the home player was shoved over their own line, and it counts"


def test_a_player_knocked_down_in_the_end_zone_does_not_score():
    """S3: "should a player with the ball be Placed Prone, Fall Over, or be Knocked
    Down as they move into the opposition End Zone, then no Touchdown will be
    scored - the player must be Standing.\""""
    from bloodbowl.pitch import LENGTH

    m = _match_st(_st("away", 7, LENGTH - 2, 3), _st("home", 7, LENGTH - 1, 3, av="12+"), active="away")
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    _block(m, "a00", "h00", _bdice(["pow"], script=[1, 1, 5]))
    assert m.score["home"] == 0, "knocked down on the line is not a Touchdown"


def test_a_touchdown_takes_the_ball_out_of_play():
    from bloodbowl.pitch import LENGTH

    m = _match(("home", 7, LENGTH - 1))
    _with_ball(m, 7, LENGTH - 1, carrier="h00")
    _move(m, "h00", 7, LENGTH, _dice([]))
    assert m.ball.in_play is False and m.ball.carrier == ""
