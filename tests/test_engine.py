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
    from bloodbowl.engine import actions

    actions.load_all()
    out = actions.get("move")["resolve"](m, {"player": pid, "x": x, "y": y}, dice)
    for e in out.events:
        m.apply(e)
    return out


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
    out = _move(m, "h00", 6, 12, _dice([2]))
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (6, 12), "a failed Dodge does not leave the player where they started"
    assert p.down == "prone"


def test_a_natural_one_fails_a_dodge_however_good_the_odds():
    m = _match(("home", 7, 13, 6, "2+"), ("away", 7, 14))
    out = _move(m, "h00", 6, 12, _dice([1]))
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
    out = _move(m, "h00", 7, 15, _dice([1]))
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
    out = _move(m, "h00", 6, 11, _dice([1]))  # Rush fails; no Dodge die scripted
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds == ["Rush"], f"a failed Rush must end it before the Dodge: {kinds}"


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
    out = _move(m, "h00", 7, 12, _dice([3]))  # AG 3+, -1 marker, -1 tail = fails
    assert not out.ok
    dodge = next(r for e in out.events for r in e.rolls if r.kind == "Dodge")
    assert dodge.modifier == -2


def test_unmodelled_skills_are_reported_rather_than_ignored():
    """A Troll's Always Hungry is not implemented. The engine says so instead of
    quietly playing as though the player did not have it."""
    m = _match(("home", 7, 13, 6, "3+", ["Always Hungry", "Mighty Blow", "Jump Up"]))
    out = _move(m, "h00", 7, 14, _dice([]))
    assert "Always Hungry" in out.unmodelled
    assert "Mighty Blow" in out.unmodelled
    assert "Jump Up" not in out.unmodelled, "Jump Up IS modelled"


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
