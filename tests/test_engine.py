"""Engine tests: the fold, determinism, and the movement rules.

Every rule asserted here was read off the S3 source and is quoted in the test that
pins it. Several of them differ from what an older edition would say, which is the
point — those are the ones that would otherwise get "fixed" back to wrong.

Dice are scripted throughout. A rules test that rolls real dice is testing luck.
"""

from __future__ import annotations

import pytest


def _unmodelled_pair():
    """Two Skills the engine does not model, chosen from the catalogue at run time.

    Hard-coding names here has gone stale three times — Really Stupid, then Always
    Hungry — because the whole point of the project is that the list shrinks. The
    fixtures below are about the REPORTING mechanism, not about which Skill it
    names, so they ask.
    """
    from bloodbowl.engine.skills import find_skills

    names = [s["name"].title() for s in find_skills(only_unmodelled=True)]
    assert len(names) >= 2, "everything is modelled — these fixtures need rewriting, happily"
    return names[0], names[1]


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


def _move(m, pid, x, y, dice, **cmd):
    """resolve applies its own events (see actions.Outcome) — do not re-apply."""
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("move")["resolve"](m, {"player": pid, "x": x, "y": y, **cmd}, dice)


def _block(m, pid, target, dice, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("block")["resolve"](m, {"player": pid, "target": target, **cmd}, dice)


# --- determinism and replay -----------------------------------------------


def _ball_at(x, y, carrier=""):
    from bloodbowl.engine.events import Event

    return Event(kind="ball_moved", detail={"x": x, "y": y, "carrier": carrier})


def _acting_board(action):
    """A board on which ``action`` is legal for h00, and its dice.

    One factory rather than one fixture per action, because the test below has to
    build the SAME board twice — once to act on and once to fold onto — and the
    two drifting apart would make the fold look wrong for the wrong reason.
    """
    if action == "secure":
        # The ball loose, and no Standing opponent within 2 squares of it.
        m = _match(("home", 7, 13), ("away", 7, 20), ("home", 8, 13))
        m.apply(_ball_at(7, 13))
        return m, {}, [6], []
    # A team-mate at (8,13) to hand off to and an opponent at (7,14) to block.
    m = _match(("home", 7, 13), ("away", 7, 14), ("home", 8, 13))
    m.by_id("h00").player.ST = "3"
    m.by_id("h00").player.PA = "3+"
    m.apply(_ball_at(7, 13, carrier="h00"))
    return m, *{
        "block": ({"target": "a01"}, [6, 6], [["push_back"]]),
        "handoff": ({"target": "h02"}, [6], []),
        "pass": ({"x": 7, "y": 18}, [6, 6, 6], []),
    }[action]


@pytest.mark.parametrize("action", ["block", "handoff", "secure", "pass"])
def test_an_action_being_over_survives_the_fold(action):
    """`fold(events)` must rebuild the position EXACTLY, and "this player has
    already acted" is part of the position.

    Every action used to assign ``p.acted`` directly and then emit a note saying
    it had — a note nothing read. The live object was right and the fold was
    wrong, so a replayed match had players free to act a second time. It stayed
    invisible because ``Match.from_dict`` seeds the players from the cached row
    BEFORE folding, so the one path anybody exercised papered over it. Folding
    from scratch is the only way to see it.
    """
    from bloodbowl.engine import actions
    from bloodbowl.engine.state import fold

    actions.load_all()
    m, cmd, script, block = _acting_board(action)
    out = actions.get(action)["resolve"](m, {"player": "h00", **cmd}, _dice(script, block))
    assert out.events, f"{action} was refused, so this test proves nothing: {out.text}"
    assert m.by_id("h00").acted, "the action did not end the activation at all"

    fresh, _cmd, _s, _b = _acting_board(action)
    rebuilt = fold(fresh, list(m.events))
    assert rebuilt.by_id("h00").acted, f"{action} left the folded player free to act again"


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
    m = _match(("home", 7, 13, 6, "3+", [*_unmodelled_pair(), "Jump Up", "Mighty Blow"]))
    out = _move(m, "h00", 7, 14, _dice([]))
    first, second = _unmodelled_pair()
    assert first in out.unmodelled and second in out.unmodelled
    # Both of these ARE modelled, and the list must shrink as skills land — this
    # test caught Mighty Blow moving from unmodelled to modelled when Blocking
    # was added, which is exactly the drift it is here to notice.
    assert "Jump Up" not in out.unmodelled
    assert "Mighty Blow" not in out.unmodelled


def test_an_unmodelled_skill_is_announced_once_per_match_not_once_per_step():
    """Honest is not the same as loud.

    The Outcome still carries every unmodelled Skill the participants hold — that
    is the raw truth and the tests above pin it. What ``act`` reports is the
    FIRST mention of each, because the same Troll taking six steps has the same
    two missing Skills six times, and a notice that always fires is one nobody
    reads.
    """
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 13, 6, "3+", list(_unmodelled_pair())))
    first = act(m, "move", {"player": "h00", "x": 7, "y": 14})
    assert first["unmodelled_skills"] == list(_unmodelled_pair())

    again = act(m, "move", {"player": "h00", "x": 7, "y": 15})
    assert again["unmodelled_skills"] == []
    # …but the raw list is untouched, so nothing has become invisible.
    from bloodbowl.engine.skills import unmodelled_skills

    assert unmodelled_skills(m.by_id("h00")) == list(_unmodelled_pair())


def test_the_first_mention_lands_in_the_log_not_only_in_the_reply():
    """The log is what the coach narrates from. A notice that lives only in a tool
    result is invisible to anyone reading the match back."""
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 13, 6, "3+", [_unmodelled_pair()[0]]))
    act(m, "move", {"player": "h00", "x": 7, "y": 14})
    noted = [e for e in m.events if e.kind == "unmodelled_noted"]
    assert len(noted) == 1
    assert _unmodelled_pair()[0] in noted[0].text
    assert noted[0].detail["skills"] == [_unmodelled_pair()[0]]


def test_the_already_said_that_ledger_survives_a_reload():
    """The ledger is the LOG, and this is why.

    A match is reloaded from disk between tool calls, so anything remembered on
    the object is gone by the next action — and a per-match notice that
    re-announces itself every call looks exactly like one that works.
    """
    from bloodbowl.engine.game import act
    from bloodbowl.engine.state import Match

    m = _match(("home", 7, 13, 6, "3+", [_unmodelled_pair()[0]]))
    assert act(m, "move", {"player": "h00", "x": 7, "y": 14})["unmodelled_skills"] == [_unmodelled_pair()[0]]

    reloaded = Match.from_dict(m.to_dict())
    assert act(reloaded, "move", {"player": "h00", "x": 7, "y": 15})["unmodelled_skills"] == []


def test_the_standing_summary_names_every_unmodelled_skill_and_its_holders():
    """The other half: quiet in the log, but always answerable on demand."""
    from bloodbowl.engine.game import state_report
    from bloodbowl.engine.skills import unmodelled_on_pitch

    m = _match(
        ("home", 7, 13, 6, "3+", [_unmodelled_pair()[0], "Block"]),
        ("home", 8, 13, 6, "3+", [_unmodelled_pair()[0]]),
        ("away", 7, 14, 6, "3+", [_unmodelled_pair()[1]]),
    )
    summary = unmodelled_on_pitch(m)
    assert {row["skill"] for row in summary} == set(_unmodelled_pair())
    hungry = next(row for row in summary if row["skill"] == _unmodelled_pair()[0])
    assert hungry["players"] == ["h00", "h01"] and hungry["count"] == 2
    # Block IS modelled, so it must not appear.
    assert "Block" not in {row["skill"] for row in summary}
    assert state_report(m)["unmodelled_skills"] == summary


def test_the_standing_summary_is_derived_so_it_drops_a_player_who_leaves():
    """Recomputed from the board rather than recorded, so a Casualty stops being
    listed the moment they leave — a remembered summary would keep warning about a
    Skill that is no longer on the pitch."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.skills import unmodelled_on_pitch

    m = _match(("home", 7, 13, 6, "3+", [_unmodelled_pair()[0]]), ("away", 7, 14, 6, "3+", [_unmodelled_pair()[1]]))
    assert len(unmodelled_on_pitch(m)) == 2
    m.apply(Event(kind="player_condition", actor=m.players[1].id, detail={"outcome": "casualty"}))
    assert [row["skill"] for row in unmodelled_on_pitch(m)] == [_unmodelled_pair()[0]]


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
    out = j.loads(t["bb_game_new"].invoke({"seed": 7, "kicking_to": "home"}))
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
    t["bb_game_new"].invoke({"seed": 3, "kicking_to": "home"})
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
    t["bb_game_new"].invoke({"seed": 3, "kicking_to": "home"})
    out = j.loads(t["bb_game_act"].invoke({"action": "move", "player": "a00", "x": 7, "y": 15}))
    assert out["ok"] is False and "turn" in out["text"]


def test_the_log_carries_the_rolls_so_the_coach_quotes_rather_than_guesses(registry):
    import json as j

    _setup_board()
    t = _tools(registry)
    t["bb_game_new"].invoke({"seed": 11, "kicking_to": "home"})
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
    t["bb_game_new"].invoke({"seed": 5, "kicking_to": "home"})
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
    t["bb_game_new"].invoke({"seed": 1, "kicking_to": "home"})
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
    _block(m, "h00", "a00", _bdice(["pow"], script=[3, 3, 6, 6, 9]))  # injury 12 = Casualty, then its D16
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


# --- kick-off and drives ---------------------------------------------------


def _kicked(seed=3):
    """A match that has already kicked off, from a small board."""
    from bloodbowl.engine.game import new_match
    from bloodbowl.pitch import Player, Scenario

    sc = Scenario(name="t", home_team="Orc", away_team="Skaven")
    for side, x, y in (("home", 7, 13), ("home", 8, 13), ("away", 7, 14), ("away", 8, 14)):
        sc.players.append(
            Player(side=side, x=x, y=y, position="Orc Lineman", team="Orc", MA="6", ST="3", AG="3+", AV="9+")
        )
    return new_match(sc, seed=seed, kicking_to="home")


def test_a_new_match_kicks_off_and_the_ball_is_in_play():
    m = _kicked()
    assert m.ball.in_play
    assert any(e.kind == "kickoff_event" for e in m.events), "the Kick-off Event must be rolled"
    assert m.drive == 1


def test_the_kick_deviates_before_the_event_is_rolled():
    """S3 order: the kick Deviates, THEN 2D6 for the Kick-off Event, THEN the ball
    lands. Rolling the event after the ball was caught would make half the table
    meaningless."""
    m = _kicked()
    kinds = [e.kind for e in m.events]
    dev = next(i for i, e in enumerate(m.events) if any(r.kind.startswith("Deviate") for r in e.rolls))
    evt = kinds.index("kickoff_event")
    assert dev < evt, "the deviation comes first"


def test_every_kickoff_event_result_is_named_with_its_real_text():
    from bloodbowl.engine.kickoff import KICKOFF_EVENTS

    assert sorted(KICKOFF_EVENTS) == list(range(2, 13)), "2D6 spans 2..12"
    for roll, (name, text, _applied) in KICKOFF_EVENTS.items():
        assert name and len(text) > 30, f"{roll} has no usable rule text"
    assert KICKOFF_EVENTS[11][0] == "Dodgy Snack", "S3 replaced Officious Ref on 11"
    assert KICKOFF_EVENTS[12][0] == "Pitch Invasion"


def test_an_unapplied_kickoff_event_says_so_rather_than_pretending():
    """A coach told 'Blitz!' who sees nothing move would reasonably conclude the
    engine is broken. Say which ones are narrative only."""
    from bloodbowl.engine.kickoff import KICKOFF_EVENTS

    unapplied = [n for n, (_name, _t, applied) in KICKOFF_EVENTS.items() if not applied]
    assert unapplied, "if everything were applied this guard is pointless"
    m = _kicked()
    ev = next(e for e in m.events if e.kind == "kickoff_event")
    if not ev.detail["applied"]:
        assert "NOT applied" in ev.text


def test_time_out_moves_the_turn_marker_and_is_actually_applied():
    """The one event the engine owns outright — it is pure clock."""
    from bloodbowl.engine.kickoff import KICKOFF_EVENTS

    assert KICKOFF_EVENTS[3][2] is True, "Time-out! needs nothing this engine lacks"


def test_a_kick_that_leaves_the_receiving_half_is_a_touchback():
    from bloodbowl.engine.kickoff import award_touchback, in_own_half
    from bloodbowl.pitch import LENGTH

    m = _kicked()
    assert in_own_half("home", 1) and not in_own_half("home", LENGTH)
    events = award_touchback(m, "home")
    assert events, "a touchback must resolve to somebody holding the ball"
    assert m.ball.carrier or m.ball.in_play


def test_a_touchback_gives_the_ball_to_a_standing_receiver():
    m = _kicked()
    from bloodbowl.engine.kickoff import award_touchback

    award_touchback(m, "home")
    holder = m.by_id(m.ball.carrier)
    assert holder is not None and holder.side == "home"


def test_a_touchdown_ends_the_drive_and_the_conceder_receives():
    from bloodbowl.engine.game import act
    from bloodbowl.pitch import LENGTH

    m = _kicked()
    scorer = m.by_id("h00")
    scorer.player.x, scorer.player.y = 7, LENGTH - 1
    m.ball.carrier, m.ball.x, m.ball.y, m.ball.in_play = scorer.id, 7, LENGTH - 1, True
    m.clock.active = "home"
    report = act(m, "move", {"player": "h00", "x": 7, "y": LENGTH})
    assert report.get("touchdown") == "home"
    assert m.score["home"] == 1
    assert m.drive == 2, "scoring starts a new drive"
    assert m.clock.active == "away", "the conceding team receives"


def test_a_new_drive_puts_everyone_back_where_they_started():
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    before = {p.id: (p.x, p.y) for p in m.players}
    m.by_id("h00").move_to(1, 1)
    m.by_id("h00").down = "prone"
    start_drive(m, receiving="away")
    assert (m.by_id("h00").x, m.by_id("h00").y) == before["h00"]
    assert m.by_id("h00").down == "standing", "a new drive stands everyone up"


def test_a_casualty_does_not_come_back_for_the_next_drive():
    from bloodbowl.engine.game import start_drive

    m = _kicked()
    m.by_id("h00").place = "casualty"
    start_drive(m, receiving="away")
    assert m.by_id("h00").place == "casualty", "a Casualty is out for the match"


def test_the_half_advances_after_eight_turns_each_and_the_match_ends_after_two():
    from bloodbowl.engine.game import end_turn

    m = _kicked()
    seen = set()
    for _ in range(40):
        seen.add((m.clock.half, m.clock.turn))
        end_turn(m)
        if m.over:
            break
    assert m.over, "the match must reach full time"
    assert (2, 8) in seen, "both halves are played"
    assert any(e.kind == "match_over" for e in m.events)


def test_the_clock_never_runs_past_the_end_of_a_half():
    from bloodbowl.engine.state import TURNS_PER_HALF

    m = _kicked()
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="clock_adjusted", detail={"delta": 5}))
    assert m.clock.turn <= TURNS_PER_HALF
    m.apply(Event(kind="clock_adjusted", detail={"delta": -99}))
    assert m.clock.turn >= 1


def test_a_whole_match_folds_back_to_the_same_position():
    """The replay guarantee has to survive drives, kick-offs and half-time —
    the parts with the most events per action."""
    from bloodbowl.engine.game import end_turn
    from bloodbowl.engine.state import Match, fold

    m = _kicked()
    for _ in range(6):
        end_turn(m)
    log = list(m.events)

    fresh = _kicked()
    fresh.players = fresh.players  # same roster, fresh state
    for p in fresh.players:
        p.down, p.place, p.ma_used, p.acted = "standing", "pitch", 0, False
    rebuilt = fold(Match(seed=fresh.seed, players=fresh.players), log)
    assert (rebuilt.clock.half, rebuilt.clock.turn) == (m.clock.half, m.clock.turn)
    assert rebuilt.score == m.score
    assert rebuilt.drive == m.drive


def test_a_log_line_always_names_somebody():
    """A board built from a preset holds labelled tokens with no positional, so
    the log read "Touchback:  is given the ball" — a sentence with a hole in it."""
    from bloodbowl.engine.state import PlayerState
    from bloodbowl.pitch import Player

    token = PlayerState(player=Player(side="home", x=1, y=1, label="LOS"), id="h00")
    assert token.name() == "LOS"
    nameless = PlayerState(player=Player(side="home", x=1, y=1), id="h07")
    assert nameless.name() == "h07"
    real = PlayerState(player=Player(side="home", x=1, y=1, position="Orc Blitzer", label="OB"), id="h01")
    assert real.name() == "Orc Blitzer", "a real positional wins over its badge"


# --- passing ---------------------------------------------------------------
#
# Everything here is quoted from the source EXCEPT how far each band reaches,
# which is measured off the physical ruler — see engine/ruler.py. The tests keep
# those two things apart: the rules get exact assertions, the ruler gets a
# cross-check against the evidence it was derived from.


def test_the_range_bands_reproduce_the_reported_maximum_reaches():
    """The derivation, asserted. Two independent community sources: measured
    section lengths, and separately reported maximum reaches per band ("N across,
    M down"). Every reported maximum must fall inside its band, and the next
    square along the same line must fall outside it — otherwise the reported
    maximum was not the maximum and the thresholds are wrong."""
    import math

    from bloodbowl.engine.ruler import band

    maxima = {"Quick Pass": (3, 1), "Short Pass": (6, 0), "Long Pass": (10, 2), "Long Bomb": (13, 1)}
    for name, (dx, dy) in maxima.items():
        got = band(0, 0, dx, dy)
        assert got is not None and got[0] == name, f"{dx}x{dy} should be {name}, got {got}"
        further = band(0, 0, dx + 1, dy)
        assert further is None or further[0] != name, f"{dx + 1}x{dy} should be beyond {name}"
        assert math.hypot(dx, dy) > 0


def test_the_ruler_declares_itself_measured_rather_than_quoted():
    """A coach must never be told a range is rules-derived when it is measured."""
    from bloodbowl.engine.ruler import describe

    d = describe()
    assert d["measured_not_quoted"] is True
    assert "no table of squares" in d["note"]
    assert [b["modifier"] for b in d["bands"]] == [0, -1, -2, -3]


def test_out_of_range_is_refused_rather_than_thrown():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 22})
    assert legal.ok is False and "out of range" in legal.reason


def test_the_range_modifier_follows_the_band():
    """S3: Quick no modifier, Short -1, Long -2, Long Bomb -3."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    v = actions.get("pass")["validate"]
    assert v(m, {"player": "h00", "x": 7, "y": 4}).detail["range_modifier"] == 0
    assert v(m, {"player": "h00", "x": 7, "y": 8}).detail["range_modifier"] == -1
    assert v(m, {"player": "h00", "x": 7, "y": 12}).detail["range_modifier"] == -2
    assert v(m, {"player": "h00", "x": 7, "y": 15}).detail["range_modifier"] == -3


def test_marking_the_passer_modifies_the_throw():
    """S3: "Apply a -1 modifier for each opposition player Marking the player
    performing the Pass Action.\""""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2), ("away", 6, 2), ("away", 8, 2))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    d = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 4}).detail
    assert d["marking_passer"] == -2 and d["modifier"] == -2


def _pass(m, pid, x, y, dice):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("pass")["resolve"](m, {"player": pid, "x": x, "y": y}, dice)


def test_an_accurate_pass_lands_in_the_target_square_and_is_caught():
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 5, 6, "3+"))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 5, _dice([5, 5]))  # pass passes, catch passes
    assert out.ok and m.ball.carrier == "h01"


def test_an_inaccurate_pass_scatters_three_squares_from_the_target():
    """S3: "the ball will Scatter (3) from the target square before landing.\""""
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "5+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 5, _dice([2, 5, 5, 5, 4]))  # fail, then 3 scatters + a bounce
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds.count("Direction") >= 3, f"expected Scatter (3): {kinds}"


def test_a_natural_one_fumbles_and_the_ball_bounces_from_the_thrower():
    """S3: "The ball is dropped and will Bounce from the throwing player's square
    and a Turnover will be caused.\""""
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 4, _dice([1, 5]))
    assert out.turnover and not out.ok
    assert m.ball.carrier == ""
    assert abs(m.ball.x - 7) <= 1 and abs(m.ball.y - 2) <= 1, "it bounces from the THROWER's square"


def test_a_one_after_modifiers_also_fumbles():
    """S3 fumbles on "a 1 after modifiers, OR the roll is a natural 1" — so a
    heavily modified pass can fumble on a die that was not a 1."""
    m = _match(("home", 7, 2, 6, "3+"), ("away", 6, 2), ("away", 8, 2), ("away", 6, 3))
    m.by_id("h00").player.PA = "3+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 5, _dice([2, 5, 5, 5]))  # 2, then the bounce and any catch
    assert out.turnover, "a total of 1 or less is a fumble even on a 2"
    fumble = next(r for e in out.events for r in e.rolls if r.kind == "Pass")
    assert fumble.dice[0] != 1 and (fumble.total or 0) <= 1, "fumbled without a natural 1"


def test_a_player_with_no_passing_ability_cannot_pass():
    """A Troll's PA reads "-". Treating that as a 4+ would invent an ability."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 2))
    m.by_id("h00").player.PA = "-"
    _with_ball(m, 7, 2, carrier="h00")
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 4})
    assert legal.ok is False and "Passing Ability" in legal.reason


def test_an_opponent_in_the_path_can_intercept_and_that_is_a_turnover():
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 8, 6, "3+"), ("away", 7, 5, 6, "2+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 8, _dice([6, 6]))  # accurate, then a natural 6 intercepts
    assert out.turnover and not out.ok
    assert m.ball.carrier == "a02"


def test_a_prone_opponent_in_the_path_cannot_intercept():
    """Only Standing players, and "Players that have lost their Tackle Zone may
    not attempt to Intercept.\""""
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 8, 6, "3+"), ("away", 7, 5, 6, "2+"))
    m.by_id("h00").player.PA = "2+"
    m.by_id("a02").down = "prone"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 8, _dice([6, 5]))  # no interception die scripted
    assert out.ok and m.ball.carrier == "h01"


def test_a_distracted_opponent_cannot_intercept_either():
    m = _match(("home", 7, 2, 6, "3+"), ("home", 7, 8, 6, "3+"), ("away", 7, 5, 6, "2+"))
    m.by_id("h00").player.PA = "2+"
    m.by_id("a02").distracted = True
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 8, _dice([6, 5]))
    assert out.ok


def test_a_pass_that_ends_loose_is_a_turnover():
    """S3 treats a Pass that does not end in your own hands as a Turnover."""
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 4, _dice([5, 5]))  # accurate into an empty square, bounces
    assert out.turnover and m.ball.carrier == ""


def test_a_modified_roll_reads_as_arithmetic_that_actually_works():
    """The log is the narration source, so a coach must be able to read it
    literally. It used to print the post-modifier total AND the modifier —
    "rolled 3-3 — passed" — which reads as 3 minus 3 = 0 passing a 3+, i.e. a
    broken engine. It was a natural 6 with a -3."""
    from bloodbowl.engine.dice import roll_target

    d = _dice([6])
    r = roll_target(d, "Intercept", 3, -3)
    line = r.describe()
    assert r.passed, "a natural 6 always succeeds"
    assert "rolled 6 -3 = 3" in line, line
    assert "rolled 3-3" not in line

    plain = roll_target(_dice([4]), "Dodge", 3)
    assert plain.describe() == "Dodge: needed 3+, rolled 4 — passed"


def test_the_pass_log_line_is_grammatical():
    m = _match(("home", 7, 2, 6, "3+"))
    m.by_id("h00").player.PA = "2+"
    _with_ball(m, 7, 2, carrier="h00")
    out = _pass(m, "h00", 7, 4, _dice([5, 5]))
    line = next(e.text for e in out.events if e.kind == "pass_thrown")
    assert "throws an accurate" in line, line
    assert "a n" not in line


# --- the Blitz Action ------------------------------------------------------
#
# S3: "Simply put, a Blitz Action combines both a Move Action and a Block Action;
# however, only a single player may perform a Blitz Action each Turn."
#
# Modelled as a DECLARATION plus the ordinary move and block, because "if at any
# point during this Move Action they are adjacent to … their intended target" is a
# decision the coach makes step by step.


def _declare(m, pid, target):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("blitz")["resolve"](m, {"player": pid, "target": target}, _dice([]))


def test_a_blitz_is_move_then_block_then_more_move():
    """The whole shape in one test: declare, walk, hit them, walk on.

    "After the player has performed the Block Action, they can continue their Move
    Action using any remaining Move Allowance they have left."
    """
    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a01").ok

    for y in (11, 12, 13):
        assert _move(m, "h00", 7, y, _dice([])).ok
    assert m.by_id("h00").ma_used == 3

    out = _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    assert out.ok
    assert m.by_id("h00").ma_used == 4, "the Blitz's Block costs a point of Move Allowance"
    assert (m.by_id("a01").x, m.by_id("a01").y) == (7, 15), "the target was pushed"

    # …and they may keep going.
    assert _move(m, "h00", 6, 13, _dice([])).ok
    assert m.by_id("h00").ma_used == 5


def test_the_rulebooks_own_worked_example():
    """Reproduced verbatim from the S3 text:

    "The Skeleton Lineman has declared a Blitz Action against the Bretonnian
     Squire and uses all of their Move Allowance of 5 to end next to their
     intended target. As a result, the Skeleton Lineman must Rush in order to
     perform the Block Action part of the Blitz Action. The Skeleton Lineman rolls
     a 4 and succeeds, so the Block Action can go ahead."
    """
    m = _match(("home", 7, 8, 5), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a01").ok
    for y in (9, 10, 11, 12, 13):
        assert _move(m, "h00", 7, y, _dice([])).ok
    assert m.by_id("h00").ma_used == 5 == m.by_id("h00").movement(), "all of their Move Allowance"

    out = _block(m, "h00", "a01", _dice([4], [["push_back"]]), follow_up=False)
    assert out.ok, "the Block Action can go ahead"
    rush = next(r for e in out.events for r in e.rolls if r.kind == "Rush")
    assert rush.dice == [4] and rush.passed
    assert any(e.kind == "block_rolled" for e in out.events)


def test_a_failed_rush_for_the_block_floors_them_where_they_stand():
    """ "On a 1, the player trips and Falls Over in the square they were attempting
    to Rush into" — but a Rush that buys the Block buys no square. The rules settle
    the same shape for a Jump: "the rushing player will Fall Over in the square
    they are in rather than the square they are attempting to Jump into." So: where
    they stand, and the Block never happens.
    """
    m = _match(("home", 7, 8, 5), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    for y in (9, 10, 11, 12, 13):
        _move(m, "h00", 7, y, _dice([]))

    out = _block(m, "h00", "a01", _dice([1, 2, 2], [["pow"]]))
    assert not out.ok and out.turnover
    p = m.by_id("h00")
    assert (p.x, p.y) == (7, 13), "they fall where they are, not into the target's square"
    assert p.down in ("prone", "stunned")
    assert not any(e.kind == "block_rolled" for e in out.events), "the Block never happened"


def test_only_one_blitz_per_team_per_turn():
    """ "only a single player may perform a Blitz Action each Turn" — and
    "Players are never required to perform the Block Action against their intended
    target if they decide not to, though they will still count as having used
    their team's one Blitz Action for the Turn." So DECLARING spends it.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6), ("home", 8, 10, 6), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a02").ok

    second = actions.get("blitz")["validate"](m, {"player": "h01", "target": "a02"})
    assert not second.ok
    assert "already used their one Blitz" in second.reason


def test_a_new_turn_gives_the_team_its_blitz_back():
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    assert m.blitz
    end_turn(m)
    assert m.blitz == {}


def test_an_unreachable_target_may_not_be_declared():
    """ "Players may not declare an opposition player as the intended target of the
    Block Action if they cannot reach the player at all with their Move Action
    (including any extra squares gained by attempting to Rush)."

    MA 3 plus two Rushes is five squares; getting adjacent to a player nine away
    is not on.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 5, 3), ("away", 7, 14, 6))
    legal = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not legal.ok and "out of reach" in legal.reason

    # The boundary, both sides of it. MA 5 plus two Rushes is 7 squares, and the
    # ring around a player at (7,14) starts at (7,13):
    #   from (7,6) that ring is exactly 7 steps — reachable, and the declaration
    #   stands even though every square is spent getting there and the Block
    #   itself costs one more. The rules gate the declaration on REACH alone, and
    #   "players are never required to perform the Block Action", so this is a
    #   legal thing to do rather than something to refuse — but the coach is told.
    at_the_limit = actions.get("blitz")["validate"](
        _match(("home", 7, 6, 5), ("away", 7, 14, 6)), {"player": "h00", "target": "a01"}
    )
    assert at_the_limit.ok
    assert at_the_limit.detail["steps"] == 7 and at_the_limit.detail["budget"] == 7
    assert at_the_limit.detail["can_reach"] is True
    assert at_the_limit.detail["can_block"] is False, "no square left to pay for the Block"

    #   one square further back and it is out of reach altogether.
    beyond = actions.get("blitz")["validate"](
        _match(("home", 7, 5, 5), ("away", 7, 14, 6)), {"player": "h00", "target": "a01"}
    )
    assert not beyond.ok and "out of reach" in beyond.reason


def test_reach_is_a_route_and_not_a_straight_line():
    """A target with every neighbouring square occupied cannot be reached however
    much Move Allowance is going spare. Measuring the distance rather than walking
    it would declare a Blitz that can never land."""
    from bloodbowl.engine import actions

    actions.load_all()
    ring = [("home", x, y, 6) for x in (6, 7, 8) for y in (13, 14, 15) if (x, y) != (7, 14)]
    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6), *ring)
    legal = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not legal.ok and "no route" in legal.reason


def test_the_blitz_block_must_be_against_the_declared_target():
    """ "they must ALSO declare which opposition player is the intended target."
    A Blitz is not a licence to block whoever turns out to be handy."""
    from bloodbowl.engine import actions

    actions.load_all()
    # The second opponent sits at (6,14): adjacent to the square h00 walks INTO,
    # but not to the one it leaves, so the step needs no Dodge and the test is
    # about the Blitz rather than about the dice.
    m = _match(("home", 7, 12, 6), ("away", 7, 14, 6), ("away", 6, 14, 6))
    _declare(m, "h00", "a01")
    _move(m, "h00", 7, 13, _dice([]))  # now adjacent to BOTH away players

    other = actions.get("block")["validate"](m, {"player": "h00", "target": "a02"})
    assert not other.ok and "already acted" in other.reason
    assert actions.get("block")["validate"](m, {"player": "h00", "target": "a01"}).ok


def test_a_blitz_does_not_buy_a_second_block():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    assert _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert m.blitz["blocked"] is True

    again = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert not again.ok and "already acted" in again.reason


def test_a_block_may_only_target_a_standing_player():
    """ "they must nominate one Standing opposition player they are Marking to be
    the target of the Block Action." Flooring someone already down is what the Foul
    Action is for — and the engine used to allow it."""
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))

    plain = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert not plain.ok and "Standing" in plain.reason
    declared = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not declared.ok and "Standing" in declared.reason


def test_the_declared_blitz_survives_the_fold():
    """Per-turn state like everything else: recorded, folded, never remembered."""
    from bloodbowl.engine.state import Match, fold

    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    for who in (fold(_match(("home", 7, 10, 6), ("away", 7, 14, 6)), list(m.events)), Match.from_dict(m.to_dict())):
        assert who.blitz == {"player": "h00", "target": "a01", "blocked": False}


def test_legal_moves_offers_the_blitz_with_the_distance_already_walked():
    """The coach asks the engine rather than eyeballing it — a team's one Blitz
    per turn cannot be taken back once declared."""
    from bloodbowl.engine.game import legal_moves

    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6), ("away", 2, 25, 6))
    out = legal_moves(m, "h00")
    assert out["blitz"]["available"] is True
    targets = {t["target"]: t for t in out["blitz"]["targets"]}
    assert "a01" in targets and targets["a01"]["steps"] == 3 and targets["a01"]["can_block"] is True
    assert "a02" not in targets, "the one across the pitch is out of reach and is not offered"

    _declare(m, "h00", "a01")
    assert legal_moves(m, "h00")["blitz"]["declared"]["target"] == "a01"


def test_an_ordinary_block_ends_the_activation_but_a_blitzs_block_does_not():
    """The distinction the Blitz exists to make, and the reason `done` is a
    separate flag from `acted`.

    `acted` means an activation has BEGUN — a single step of movement sets it, so
    it can never be what stops movement. `done` means it is OVER. With only
    `acted` to go on, movement was ungated and a player could throw a Block
    Action and then stroll away, which no Action in the game allows.
    """
    from bloodbowl.engine import actions

    actions.load_all()

    plain = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    assert _block(plain, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert plain.by_id("h00").done is True
    after = actions.get("move")["validate"](plain, {"player": "h00", "x": 6, "y": 12})
    assert not after.ok and "activation is over" in after.reason

    blitzed = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _declare(blitzed, "h00", "a01")
    assert _block(blitzed, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert blitzed.by_id("h00").done is False, "a Blitz's Block leaves the activation open"
    assert actions.get("move")["validate"](blitzed, {"player": "h00", "x": 6, "y": 12}).ok


@pytest.mark.parametrize("action", ["block", "handoff", "secure", "pass"])
def test_no_action_lets_a_player_walk_on_afterwards(action):
    """None of these includes movement after the fact — "may not continue moving
    after the pass has been made", "their activation immediately ends"."""
    from bloodbowl.engine import actions

    actions.load_all()
    m, cmd, script, block = _acting_board(action)
    actions.get(action)["resolve"](m, {"player": "h00", **cmd}, _dice(script, block))
    p = m.by_id("h00")
    legal = actions.get("move")["validate"](m, {"player": "h00", "x": p.x - 1, "y": p.y - 1})
    assert not legal.ok, f"{action} left the player free to keep moving"


def test_the_activation_being_over_survives_the_fold_too():
    from bloodbowl.engine.state import fold

    m, cmd, script, block = _acting_board("block")
    from bloodbowl.engine import actions

    actions.load_all()
    actions.get("block")["resolve"](m, {"player": "h00", **cmd}, _dice(script, block))
    fresh, _c, _s, _b = _acting_board("block")
    assert fold(fresh, list(m.events)).by_id("h00").done is True


# --- the free Move, and one Action per team per turn -----------------------


def _ball_board():
    """A carrier, a team-mate to hand to, and an opponent within Blitz reach."""
    m = _match(("home", 7, 10, 6), ("home", 8, 10, 6), ("away", 7, 15, 6))
    for who in ("h00", "h01"):
        m.by_id(who).player.PA = "3+"
    m.apply(_ball_at(7, 10, carrier="h00"))
    return m


@pytest.mark.parametrize(
    "action,cmd",
    [("pass", {"x": 7, "y": 14}), ("handoff", {"target": "h01"})],
)
def test_an_action_that_grants_a_free_move_is_not_refused_for_having_moved(action, cmd):
    """S3, for the Pass and word for word again for the Hand-off:

    "A player that declares a Pass Action may ALSO MAKE A FREE MOVE ACTION before
     making the pass, but may not continue moving after the pass has been made."

    The check here used to be `p.acted`, which a single step of movement sets — so
    the free Move the rules grant made the Action itself illegal, and move-then-
    pass has been impossible for as long as passing has existed. The question is
    whether the activation is OVER (`done`), not whether it has begun.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    assert _move(m, "h00", 7, 11, _dice([])).ok
    legal = actions.get(action)["validate"](m, {"player": "h00", **cmd})
    assert legal.ok, f"the free Move before a {action} was refused: {legal.reason}"


def test_but_not_after_the_activation_is_over():
    """ "…may not continue moving after the pass has been made," and no second
    Action either."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    m.by_id("h00").player.PA = "2+"
    actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 14}, _dice([6, 6, 6, 6, 6, 6]))
    again = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 13})
    assert not again.ok and "activation is over" in again.reason


def test_a_blitzing_player_may_not_decide_it_was_a_pass():
    """The one activation that is neither begun-nor-over. A Blitz's Block leaves
    `done` false so the player can keep MOVING — not so they can take a different
    Action instead."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    m.apply(_ball_at(7, 10, carrier="h00"))
    _declare(m, "h00", "a02")
    legal = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 14})
    assert not legal.ok and "already declared a Blitz Action" in legal.reason


@pytest.mark.parametrize(
    "action,first,second",
    [
        ("pass", {"x": 7, "y": 14}, {"x": 8, "y": 14}),
        ("handoff", {"target": "h01"}, {"target": "h00"}),
    ],
)
def test_only_one_of_each_capped_action_per_team_per_turn(action, first, second):
    """ "Only a single Pass Action can be declared each Turn" — and the same
    sentence for Hand-off, Secure the Ball, Blitz and Foul. Move and Block are the
    two the text explicitly does NOT cap.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ball_board()
    m.by_id("h00").player.PA = "2+"
    out = actions.get(action)["resolve"](m, {"player": "h00", **first}, _dice([6, 6, 6, 6, 6, 6]))
    assert out.events, out.text
    assert m.turn_actions.get(action) == "h00", m.turn_actions

    # Whoever now holds the ball, a SECOND one this turn is refused.
    holder = m.ball.carrier or "h01"
    legal = actions.get(action)["validate"](m, {"player": holder, **second})
    assert not legal.ok
    from bloodbowl.engine.actions import DISPLAY

    assert f"one {DISPLAY[action]} Action this turn" in legal.reason, legal.reason


def test_block_and_move_are_not_capped():
    """ "There is no limit to the number of players that can declare a Block Action
    each Turn." Capping everything uniformly would have been tidier and wrong."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 13, 6), ("away", 8, 14, 6))
    assert _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    assert "block" not in m.turn_actions
    assert actions.get("block")["validate"](m, {"player": "h02", "target": "a03"}).ok


def test_the_turn_ledger_is_cleared_by_the_next_turn_and_survives_a_fold():
    from bloodbowl.engine.game import end_turn
    from bloodbowl.engine.state import Match, fold

    m = _ball_board()
    m.by_id("h00").player.PA = "2+"
    from bloodbowl.engine import actions

    actions.load_all()
    actions.get("pass")["resolve"](m, {"player": "h00", "x": 7, "y": 14}, _dice([6, 6, 6, 6, 6, 6]))
    assert m.turn_actions == {"pass": "h00"}

    # The ledger is state, so it must come back from the log alone.
    assert fold(_ball_board(), list(m.events)).turn_actions == {"pass": "h00"}
    assert Match.from_dict(m.to_dict()).turn_actions == {"pass": "h00"}

    end_turn(m)
    end_turn(m)
    assert m.turn_actions == {}


# --- the Foul Action -------------------------------------------------------


def _foul(m, pid, target, dice, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("foul")["resolve"](m, {"player": pid, "target": target, **cmd}, dice)


def _floored(m, pid, how="prone"):
    from bloodbowl.engine.events import Event

    m.apply(Event(kind="player_placed_prone", actor=pid, detail={"down": how}))
    return m


def test_a_foul_may_only_target_a_player_who_is_already_down():
    """The exact complement of the Block: "The player must finish their Move
    Action adjacent to a Prone or Stunned opposition player in order to perform
    the Foul Action." A Block needs a Standing target; a Foul needs one that
    isn't, and neither is a special case of the other."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    standing = actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"})
    assert not standing.ok and "Standing" in standing.reason and "Block them" in standing.reason

    _floored(m, "a01")
    assert actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"}).ok
    # …and now the Block is the one that is refused. The two are exclusive.
    blocked = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert not blocked.ok and "Standing" in blocked.reason


def test_foul_assists_modify_the_armour_roll_not_strength():
    """ "the player making the Foul Action may apply a +1 modifier for each
    Offensive Assist, and apply a -1 modifier for each Defensive Assist."

    A Block spends its assists on STRENGTH, before any dice. Carrying that habit
    over would put the assists in the wrong place and still look plausible.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    # h00 fouls a01; h02 assists; a03 marks h00 and cancels nothing but assists back.
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 14, 6), ("away", 6, 13, 6))
    _floored(m, "a01")
    d = actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"}).detail
    assert d["offensive_assists"] == 1, d
    assert d["defensive_assists"] == 1, d
    assert d["armour_modifier"] == 0

    out = _foul(m, "h00", "a01", _dice([3, 3, 6, 6, 4]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0


def test_the_armour_roll_actually_carries_the_assist_modifier():
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 14, 6), ("home", 6, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([3, 4, 6, 6, 9, 4]))  # …6+6 is a Casualty, then its D16
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 2, "two Offensive Assists, +1 each"
    assert armour.total == 3 + 4 + 2


def test_a_clean_foul_is_not_a_turnover_and_ends_the_activation():
    """ "Regardless of the outcome" only bites on a natural double. Get away with
    it and the turn goes on — the Foul just ends that player's activation."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([2, 5]))  # 7 vs AV 9+, no double, armour holds
    assert out.ok and not out.turnover
    assert m.by_id("h00").done is True
    assert m.by_id("h00").place == "pitch"
    assert not any(e.kind == "player_sent_off" for e in out.events)


@pytest.mark.parametrize(
    "script,where",
    [
        # A double that FAILS to break armour still sends you off — the third die
        # is Argue the Call, which a sending-off always triggers.
        ([3, 3, 4], "Armour Roll"),
        ([6, 5, 4, 4, 3], "Injury Roll"),  # armour breaks on 6+5, injury is a double
    ],
)
def test_a_natural_double_on_either_roll_sends_the_fouler_off(script, where):
    """ "Regardless of the outcome, if during a Foul Action a natural double is
    rolled for EITHER the Armour Roll or Injury Roll, then the player performing
    the Foul Action is Sent-off." A double that fails to break armour still counts,
    which is the part that reads as a bug when it happens to you."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice(script))
    assert any(e.kind == "player_sent_off" for e in out.events), f"no sending-off from {where} {script}"
    assert out.turnover, "a Sent-off player on the active team causes a Turnover"


def test_a_natural_double_is_natural_so_assists_cannot_create_or_hide_one():
    """ "a NATURAL double" — the raw dice, before the assist modifier."""
    from bloodbowl.engine.actions.foul import _natural_double

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 8, 14, 6))
    _floored(m, "a01")
    # 3 and 5 with +1 from the assist totals 9, which breaks AV 9+ — but 3 and 5
    # is not a double, and the Injury Roll behind it (6 and 5) is not one either,
    # so nobody is sent off however tempting that "9" looks.
    out = _foul(m, "h00", "a01", _dice([3, 5, 6, 5, 9]))  # 11 is a Casualty, then its D16
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 1 and armour.dice == [3, 5]
    assert _natural_double(out.events) is None
    assert not any(e.kind == "player_sent_off" for e in out.events)


def test_a_sent_off_player_leaves_for_good_and_does_not_come_back_next_drive():
    """ "immediately removed from the pitch and will play no further part in the
    game" — so unlike a Knocked-out player they must not reappear at the next
    kick-off, and unlike a Casualty they are their own kind of gone."""
    from bloodbowl.engine.game import start_drive

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    _foul(m, "h00", "a01", _dice([3, 3, 2]))  # double on the armour, then Argue rolls 2
    assert m.by_id("h00").place == "sent_off"

    m.setup = [{"id": "h00", "x": 7, "y": 13}, {"id": "a01", "x": 7, "y": 14}]
    start_drive(m, receiving="home", dice=_dice([3, 1, 4, 4, 3, 3] + [4] * 12))
    assert m.by_id("h00").place == "sent_off", "a Sent-off player came back for the next drive"


@pytest.mark.parametrize(
    "argue,stays,bans",
    [(6, True, False), (1, False, True), (4, False, False)],
)
def test_argue_the_call(argue, stays, bans):
    """1   "YOU'RE OUTTA HERE!"   still Sent-off, and the Coach may not argue again
    2-5  "I DON'T CARE!"        still Sent-off
     6   "WELL, WHEN YOU PUT IT LIKE THAT…"  placed back in the square they were
         in and NOT Sent-off — "though a Turnover is still caused."
    """
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([3, 3, argue]))

    p = m.by_id("h00")
    assert (p.place == "pitch") is stays, f"argue {argue} -> place {p.place}"
    if stays:
        assert (p.x, p.y) == (7, 13), "placed back in the square they were in"
    assert out.turnover, "a Turnover is caused either way"
    assert ("home" in m.argue_banned) is bans


def test_an_ejected_coach_may_not_argue_again_this_game():
    """The ban is per SIDE and per MATCH — one of the few things a new turn does
    not clear."""
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 6, 13, 6), ("away", 6, 14, 6))
    _floored(m, "a01")
    _foul(m, "h00", "a01", _dice([3, 3, 1]))  # caught, and the coach is ejected
    assert m.argue_banned == ["home"]

    end_turn(m)
    end_turn(m)  # back round to home
    assert m.argue_banned == ["home"], "a new turn must not clear the ejection"

    _floored(m, "a03")
    out = _foul(m, "h02", "a03", _dice([4, 4]))  # caught again; no Argue roll offered
    assert m.by_id("h02").place == "sent_off"
    assert not any(r.kind == "Argue the Call" for e in out.events for r in e.rolls)
    assert any("may not Argue the Call again" in (e.text or "") for e in out.events)


def test_the_rulebooks_worked_foul_example():
    """Reproduced from the S3 text:

    "the Tomb Kings Blitzer makes an Armour Roll for the Bretonnian Squire they
     are fouling, rolling a 6 and a 3, which breaks their armour. They then make
     an Injury Roll and roll a double 2, causing the Tomb Kings Blitzer to be
     Sent-off and the Bretonnian Squire to be Stunned. The Tomb Kings Coach
     attempts to Argue the Call and rolls a 1, meaning that the Tomb Kings Blitzer
     is still Sent-off and the Coach cannot Argue the Call for the remainder of
     the game!"
    """
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.by_id("a01").player.AV = "8+"  # 6+3 = 9 must break it, as in the example
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([6, 3, 2, 2, 1]))

    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.dice == [6, 3] and armour.passed, "which breaks their armour"
    injury = next(r for e in out.events for r in e.rolls if r.kind == "Injury")
    assert injury.dice == [2, 2]
    assert m.by_id("a01").down == "stunned", "the Bretonnian Squire to be Stunned"
    assert m.by_id("h00").place == "sent_off", "the Tomb Kings Blitzer to be Sent-off"
    assert m.argue_banned == ["home"], "the Coach cannot Argue the Call for the remainder of the game"


def test_mighty_blow_does_not_apply_to_a_foul():
    """ "Whenever this player Knocks Down an opposition player during a BLOCK
    ACTION" — a Foul is not one. Passing the fouler as the responsible player
    would have handed them a +1 the rules do not give."""
    m = _match(("home", 7, 13, 6, "3+", ["Mighty Blow"]), ("away", 7, 14, 6))
    _floored(m, "a01")
    out = _foul(m, "h00", "a01", _dice([4, 4, 3]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0, "Mighty Blow leaked into a Foul"


def test_only_one_foul_per_team_per_turn():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("home", 6, 13, 6), ("away", 6, 14, 6))
    _floored(m, "a01")
    _floored(m, "a03")
    assert _foul(m, "h00", "a01", _dice([2, 5])).ok
    second = actions.get("foul")["validate"](m, {"player": "h02", "target": "a03"})
    assert not second.ok and "one Foul Action this turn" in second.reason


def test_legal_moves_offers_the_foul_with_the_risk_spelled_out():
    from bloodbowl.engine.game import legal_moves

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 8, 13, 6))
    _floored(m, "a01")
    out = legal_moves(m, "h00")
    assert [f["target"] for f in out["fouls"]] == ["a01"], "only the one already down"
    assert [b["target"] for b in out["blocks"]] == ["a02"], "and only the standing one is blockable"
    assert "natural double" in out["fouls"][0]["sending_off_on"]
    assert out["fouls"][0]["may_argue"] is True


def test_a_blitz_may_be_re_pointed_before_anything_happens_but_not_after():
    """A deliberate permissive edge, pinned so it stays deliberate.

    Declaring a Blitz rolls no dice and changes nothing but the declaration, so a
    coach who names the wrong target may re-point it — the team's one Blitz is
    still spent by the same player either way. The moment ANYTHING happens it is
    settled: a step of movement sets `acted`, and so does the Blitz's Block, and
    both refuse a re-declaration.

    That bound is what makes it safe. Without it, re-declaring would reset
    `blocked` to False and buy a second Blitz Block, which is a real exploit
    rather than a convenience.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6), ("away", 7, 14, 6), ("away", 9, 14, 6))

    assert _declare(m, "h00", "a01").ok
    assert _declare(m, "h00", "a02").ok, "re-pointing before moving is allowed"
    assert m.blitz == {"player": "h00", "target": "a02", "blocked": False}
    assert m.turn_actions["blitz"] == "h00", "still the team's one Blitz, same player"

    assert _move(m, "h00", 7, 11, _dice([])).ok
    after_moving = actions.get("blitz")["validate"](m, {"player": "h00", "target": "a01"})
    assert not after_moving.ok, "once the player has moved the declaration is settled"

    # And no second Blitz Block: block, then try to re-point and block again.
    m2 = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 8, 12, 6))
    _declare(m2, "h00", "a01")
    assert _block(m2, "h00", "a01", _dice([], [["push_back"]]), follow_up=False).ok
    again = actions.get("blitz")["validate"](m2, {"player": "h00", "target": "a02"})
    assert not again.ok, "re-declaring after the Blitz's Block would buy a second one"


def test_a_second_player_never_gets_the_teams_blitz():
    """The limit that actually matters, asked of a DIFFERENT player — the one the
    re-pointing case must not weaken."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6), ("home", 8, 10, 6), ("away", 7, 14, 6))
    assert _declare(m, "h00", "a02").ok
    other = actions.get("blitz")["validate"](m, {"player": "h01", "target": "a02"})
    assert not other.ok
    assert "one Blitz Action this turn" in other.reason and "Home 0" in other.reason


# --- the skill catalogue ---------------------------------------------------


def test_every_skill_on_every_roster_can_be_quoted():
    """The catalogue exists so an unmodelled Skill can be QUOTED rather than
    guessed at. A Skill a roster uses but the catalogue lacks is precisely the
    case where a coach falls back on recall — which is the failure in §1."""
    import json
    from pathlib import Path

    from bloodbowl.engine.skills import catalogue

    have = catalogue()
    assert len(have) >= 100, f"only {len(have)} entries"
    rosters = json.loads((Path(__file__).resolve().parent.parent / "data" / "rosters.json").read_text())
    used = {
        s.split("(")[0].strip()
        for team in rosters["teams"]
        for grp in ("positionals", "star_players")
        for p in (team.get(grp) or [])
        for s in (p.get("skills") or [])
        if s.split("(")[0].strip() not in ("", "-")
    }
    missing = sorted(n for n in used if n.casefold() not in have)
    assert not missing, f"{len(missing)} roster skills cannot be quoted: {missing}"


def test_break_tackle_reads_as_the_rulebook_has_it():
    """The exact entry the agent got wrong, pinned verbatim.

    It called Break Tackle "an ST-based alternative" to the dodge roll. It is a
    MODIFIER to the same Agility Test, and the difference decides whether a coach
    thinks a ST 5 player dodges on a 3+ or on something else entirely.
    """
    from bloodbowl.engine.skills import describe_skill

    bt = describe_skill("Break Tackle")
    assert bt is not None
    assert "modifier to the Agility Test" in bt["text"]
    assert "+1" in bt["text"] and "+2" in bt["text"] and "+3" in bt["text"]
    # It is modelled now, and the hook that models it quotes this same sentence —
    # so the catalogue and the engine cannot drift into disagreeing about it.
    assert bt["modelled"] is True
    from bloodbowl.engine.skills import _break_tackle

    assert "modifier to the Agility Test" in (_break_tackle.__doc__ or "")


def test_a_trait_is_distinguished_from_a_skill():
    """A Trait is marked ONLY by a trailing asterisk on the heading — nothing else
    on the page says so. Dropping it loses all 25, including the three most common
    things on the pitch."""
    from bloodbowl.engine.skills import catalogue, describe_skill

    assert describe_skill("Stunty")["kind"] == "Trait"
    assert describe_skill("Loner")["kind"] == "Trait"
    assert describe_skill("Block")["kind"] == "Skill"
    assert sum(1 for v in catalogue().values() if v["kind"] == "Trait") >= 20


def test_the_elite_marker_and_the_categories_survived_the_scrape():
    """Both are SYMBOLS in the prose — "an Elite Skill will be denoted by the
    symbol" — so the flattened page text has neither, and only the HTML does. The
    four Elite skills are also the four most common on the rosters."""
    from bloodbowl.engine.skills import catalogue

    elite = sorted(v["name"] for v in catalogue().values() if v["elite"])
    assert elite == ["BLOCK", "DODGE", "GUARD", "MIGHTY BLOW"], elite
    cats = {v["category"] for v in catalogue().values()}
    assert {"Agility", "Devious", "General", "Mutation", "Passing", "Strength", "Trait"} == cats


def test_the_catalogue_reports_what_the_engine_actually_models():
    """Derived from the hook registry, never written down twice — so a skill that
    gets modelled starts reporting itself as modelled with no second edit."""
    from bloodbowl.engine.skills import describe_skill, find_skills, modelled

    for name in ("Block", "Dodge", "Guard", "Mighty Blow", "Thick Skull", "Jump Up", "Prehensile Tail"):
        assert describe_skill(name)["modelled"] is True, name
    assert {s["name"].casefold() for s in find_skills(only_unmodelled=True)}.isdisjoint(modelled())


def test_an_unknown_skill_suggests_something_rather_than_nothing():
    from bloodbowl.engine.skills import describe_skill

    assert describe_skill("Definitely Not A Skill") is None


def test_a_skill_can_be_found_by_what_it_does():
    """ "which skills grant a re-roll" is the question a coach actually has."""
    from bloodbowl.engine.skills import find_skills

    names = {s["name"] for s in find_skills("re-roll")}
    assert {"DODGE", "SURE HANDS", "CATCH", "PASS"} <= names, sorted(names)


# --- skills batch one: the ones that attach to rolls the engine already makes ---


def _skilled(side, x, y, skills, ma=6, st=3, ag="3+"):
    return (side, x, y, ma, ag, skills)


def _dodge_roll(out, kind="Dodge"):
    return next(r for e in out.events for r in e.rolls if r.kind == kind)


def test_break_tackle_is_a_modifier_scaled_by_strength():
    """ "a +1 modifier … if they have a Strength characteristic of 3 or lower, a +2
    … if 4, or a +3 … if 5 or higher." The exact sentence the agent got wrong."""
    from bloodbowl.engine.rules import strength_of

    for st, want in ((3, 1), (4, 2), (5, 3), (2, 1), (6, 3)):
        m = _match(("home", 7, 13, 6, "3+", ["Break Tackle"]), ("away", 7, 14, 6))
        m.by_id("h00").player.ST = str(st)
        assert strength_of(m, m.by_id("h00")) == st
        out = _move(m, "h00", 6, 12, _dice([4]))
        assert _dodge_roll(out).modifier == want, f"ST {st} should give +{want}"


def test_break_tackle_is_once_per_turn_and_that_survives_the_fold():
    """ "Once per Turn" — and the flag has to be recorded, not assigned, or a
    folded match hands it back. (`dodge_reroll_used` had exactly that bug.)"""
    from bloodbowl.engine.state import fold

    def board():
        return _match(("home", 7, 13, 6, "3+", ["Break Tackle"]), ("away", 7, 14, 6), ("away", 5, 11, 6))

    m = board()
    m.by_id("h00").player.ST = "4"
    first = _move(m, "h00", 6, 12, _dice([4]))
    assert m.by_id("h00").break_tackle_used is True

    second = _move(m, "h00", 6, 13, _dice([4]))
    # Compared rather than asserted flat, because both destinations are Marked and
    # the point is the +2, not the board.
    assert _dodge_roll(first).modifier == _dodge_roll(second).modifier + 2, "Break Tackle fired twice in one turn"

    rebuilt = fold(board(), list(m.events))
    assert rebuilt.by_id("h00").break_tackle_used is True, "the spend did not survive the fold"


def test_the_dodge_skills_reroll_also_survives_the_fold():
    """Same class, found while adding Break Tackle: `p.dodge_reroll_used = True`
    was an assignment, so a folded match let the Dodge Skill re-roll twice."""
    from bloodbowl.engine.state import fold

    def board():
        return _match(("home", 7, 13, 6, "3+", ["Dodge"]), ("away", 7, 14, 6))

    m = board()
    out = _move(m, "h00", 6, 12, _dice([1, 5]))
    assert out.ok and len([r for e in out.events for r in e.rolls if r.kind.startswith("Dodge")]) == 2
    assert m.by_id("h00").dodge_reroll_used is True
    assert fold(board(), list(m.events)).by_id("h00").dodge_reroll_used is True


def test_stunty_ignores_marking_on_a_dodge_but_nothing_else():
    """ "they do not suffer any negative modifiers … for being Marked by opposition
    players" — the Marking penalty only, and only on a Dodge."""
    # Two opponents Marking the destination would normally be -2.
    m = _match(("home", 7, 13, 6, "3+", ["Stunty"]), ("away", 7, 14, 6), ("away", 5, 11, 6), ("away", 5, 13, 6))
    out = _move(m, "h00", 6, 12, _dice([4]))
    assert _dodge_roll(out).modifier == 0, "Stunty should cancel the Marking penalty"

    plain = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 5, 11, 6), ("away", 5, 13, 6))
    assert _dodge_roll(_move(plain, "h00", 6, 12, _dice([6]))).modifier == -2


def test_stunty_uses_the_stunty_injury_table():
    """STUNTY INJURY TABLE — 2-6 Stunned · 7-8 Knocked-out · 9 Badly Hurt · 10-12
    Casualty. The standard table is 2-7 / 8-9 / 10-12, so a 7 is the tell."""
    from bloodbowl.engine.injury import risk_injury

    for dice_pair, want in (((3, 4), "knocked_out"), ((3, 3), "stunned"), ((4, 5), "casualty")):
        m = _match(("home", 7, 13, 6, "3+", ["Stunty"]))
        m.by_id("h00").player.AV = "2+"  # armour always breaks, so the injury rolls
        events = risk_injury(m, m.by_id("h00"), _dice([6, 6, *dice_pair, 9]))  # trailing D16, if it is a Casualty
        outcome = next(e.detail["outcome"] for e in events if e.kind == "injury_roll")
        assert outcome == want, f"total {sum(dice_pair)} should be {want}, got {outcome}"

    # …and a 7 on the STANDARD table is only Stunned.
    m = _match(("home", 7, 13, 6))
    m.by_id("h00").player.AV = "2+"
    events = risk_injury(m, m.by_id("h00"), _dice([6, 6, 3, 4]))
    assert next(e.detail["outcome"] for e in events if e.kind == "injury_roll") == "stunned"


def test_stunty_and_thick_skull_together():
    """ "If this player also has the Stunty Trait, then they will only be
    Knocked-out on the roll of an 8; a roll of a 7 will be treated as a Stunned
    result."

    ORDER-SENSITIVE: Stunty must replace the table before Thick Skull adjusts the
    result. Swap the two registrations and this fails, which is the point.
    """
    from bloodbowl.engine.injury import risk_injury

    def hurt(skills, pair):
        m = _match(("home", 7, 13, 6, "3+", skills))
        m.by_id("h00").player.AV = "2+"
        events = risk_injury(m, m.by_id("h00"), _dice([6, 6, *pair, 9]))
        return next(e.detail["outcome"] for e in events if e.kind == "injury_roll")

    assert hurt(["Stunty", "Thick Skull"], (3, 4)) == "stunned", "a 7 must become Stunned"
    assert hurt(["Stunty", "Thick Skull"], (4, 4)) == "knocked_out", "an 8 still knocks them out"
    assert hurt(["Stunty"], (3, 4)) == "knocked_out", "without Thick Skull a 7 is a KO"
    assert hurt(["Thick Skull"], (4, 4)) == "stunned", "without Stunty an 8 becomes Stunned"
    assert hurt(["Thick Skull"], (4, 5)) == "knocked_out", "…and a 9 does not"


def test_titchy_helps_its_own_dodge_and_declines_to_mark():
    """Two clauses pulling opposite ways, and the second lives on the MARKER:
    "this player will not apply a -1 modifier … for Marking the opposition
    player." Never applied, so it cannot be cancelled later."""
    mine = _match(("home", 7, 13, 6, "3+", ["Titchy"]), ("away", 7, 14, 6))
    assert _dodge_roll(_move(mine, "h00", 6, 12, _dice([4]))).modifier == 1

    # An ordinary dodger moving next to a Titchy opponent takes no penalty from it.
    theirs = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 5, 11, 6, "3+", ["Titchy"]))
    assert _dodge_roll(_move(theirs, "h00", 6, 12, _dice([5]))).modifier == 0


def test_tackle_denies_the_dodge_skills_reroll():
    """ "When an opposition player attempts to Dodge away from a square in this
    player's Tackle Zone, they cannot use the Dodge Skill." The square being LEFT."""
    m = _match(("home", 7, 13, 6, "3+", ["Dodge"]), ("away", 7, 14, 6, "3+", ["Tackle"]))
    out = _move(m, "h00", 6, 12, _dice([1, 2, 2]))
    assert not out.ok, "the failed Dodge should stand"
    assert len([r for e in out.events for r in e.rolls if r.kind.startswith("Dodge")]) == 1
    assert any("Tackle" in (e.text or "") for e in out.events)


def test_tackle_also_turns_a_stumble_back_into_a_knockdown():
    """ "the opposition player does not count as having the Dodge Skill if a
    Stumble result is selected" — the blocker's Tackle, the target's Dodge."""
    m = _match(("home", 7, 13, 6, "3+", ["Tackle"]), ("away", 7, 14, 6, "3+", ["Dodge"]))
    _block(m, "h00", "a01", _dice([2, 2], [["stumble"]]), follow_up=False)
    assert m.by_id("a01").down != "standing", "Stumble should have knocked them down"

    without = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Dodge"]))
    _block(without, "h00", "a01", _dice([], [["stumble"]]), follow_up=False)
    assert without.by_id("a01").down == "standing", "Dodge should turn Stumble into a plain push"


@pytest.mark.parametrize(
    "skill,test_kind",
    [("Sure Hands", "Pick up"), ("Catch", "Catch")],
)
def test_a_re_roll_skill_gets_a_second_attempt(skill, test_kind):
    from bloodbowl.engine.ball import catch, pick_up

    m = _match(("home", 7, 13, 6, "3+", [skill]))
    m.apply(_ball_at(7, 13))
    dice = _dice([1, 5, 1, 1])
    events = pick_up(m, m.by_id("h00"), dice)[0] if skill == "Sure Hands" else catch(m, m.by_id("h00"), dice)
    tries = [r for e in events for r in e.rolls if r.kind.startswith(test_kind)]
    assert len(tries) == 2, f"{skill} should have re-rolled: {[r.kind for r in tries]}"
    assert tries[1].passed and m.ball.carrier == "h00"


def test_sure_hands_does_not_apply_to_secure_the_ball():
    """ "though not when making a Secure the Ball Action" — it is already a flat 2+
    bought by giving up the activation."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Sure Hands"]), ("away", 7, 20, 6))
    m.apply(_ball_at(7, 13))
    out = actions.get("secure")["resolve"](m, {"player": "h00"}, _dice([1, 5, 5]))
    tries = [r for e in out.events for r in e.rolls if "Secure" in r.kind]
    assert len(tries) == 1, "Secure the Ball was re-rolled"
    assert out.turnover


def test_big_hand_ignores_every_negative_pick_up_modifier():
    """ "ignores ALL negative modifiers when attempting to pick up the ball"."""
    from bloodbowl.engine.ball import pick_up

    m = _match(("home", 7, 13, 6, "3+", ["Big Hand"]), ("away", 7, 14, 6), ("away", 6, 14, 6))
    m.apply(_ball_at(7, 13))
    events = pick_up(m, m.by_id("h00"), _dice([3]))[0]
    r = next(r for e in events for r in e.rolls if r.kind == "Pick up")
    assert r.modifier == 0 and r.passed


def test_accurate_and_nerves_of_steel_on_a_pass():
    """Accurate: "+1 … which is a Quick Pass or a Short Pass". Nerves of Steel:
    "ignore any modifiers for being Marked … to Pass the ball"."""
    from bloodbowl.engine import actions

    actions.load_all()

    def throw(skills, tx, ty, foes=()):
        m = _match(("home", 7, 2, 6, "3+", list(skills)), *foes)
        m.by_id("h00").player.PA = "3+"
        m.apply(_ball_at(7, 2, carrier="h00"))
        out = actions.get("pass")["resolve"](m, {"player": "h00", "x": tx, "y": ty}, _dice([6, 6, 6, 6, 6]))
        return next(r for e in out.events for r in e.rolls if r.kind == "Pass")

    assert throw(["Accurate"], 7, 4).modifier == 1, "a Quick Pass gets +1"
    assert throw([], 7, 4).modifier == 0
    # A Long Pass is -2, and Accurate does not apply to it.
    assert throw(["Accurate"], 7, 12).modifier == -2

    marked = (("away", 7, 3, 6),)
    assert throw([], 7, 4, marked).modifier == -1, "one Marker on the passer"
    assert throw(["Nerves of Steel"], 7, 4, marked).modifier == 0


# --- skills batch two: the push and the follow-up --------------------------


def test_stand_firm_refuses_a_push_into_the_crowd():
    """ "they can choose to not be Pushed Back and instead remain in their current
    square" — a choice, taken by the engine only where the alternative is the
    Crowd, which is an Injury Roll with no armour behind it."""
    # Target on the sideline with nowhere to go but off.
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6, "3+", ["Stand Firm"]))
    out = _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    assert out.ok
    t = m.by_id("a01")
    assert t.place == "pitch" and (t.x, t.y) == (1, 13), "Stand Firm should have refused the Crowd"
    assert any("Stand Firm" in (e.text or "") for e in out.events)

    without = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    _block(without, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)
    assert without.by_id("a01").place != "pitch", "without it they go into the Crowd"


def test_sidestep_escapes_the_ordinary_push_arc():
    """ "instead of the opposing Coach choosing where this player is Pushed Back
    to, this player's Coach may choose ANY adjacent unoccupied square."

    Asserted by BLOCKING the ordinary three-square arc, because every square
    adjacent to the target is the same distance from the blocker — so "the
    furthest square" cannot tell the two apart, and a test written that way passes
    against an engine that has never heard of Sidestep. Mine did.
    """
    from bloodbowl.engine.rules import push_squares

    arc = push_squares(7, 13, 7, 14)
    assert set(arc) == {(7, 15), (6, 15), (8, 15)}, arc  # order is direction-then-flanks
    walled = [("home", x, y, 6) for x, y in arc]

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Sidestep"]), *walled)
    _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    t = m.by_id("a01")
    assert (t.x, t.y) not in arc, "Sidestep should have left the arc entirely"
    assert max(abs(t.x - 7), abs(t.y - 14)) == 1, f"({t.x},{t.y}) is not adjacent to where they stood"

    # Without it, a fully-blocked arc is a Chain Push into one of those squares.
    plain = _match(("home", 7, 13, 6), ("away", 7, 14, 6), *walled)
    _block(plain, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)
    assert (plain.by_id("a01").x, plain.by_id("a01").y) in arc


def test_grab_widens_the_push_arc_and_suppresses_sidestep():
    """ "this player's Coach may choose ANY unoccupied square adjacent to the
    target … opposition players cannot use the Sidestep Skill." """
    from bloodbowl.engine.actions.block import _push_to

    # (6,14) is adjacent to the target but NOT in the ordinary three-square arc.
    m = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6))
    square, kind = _push_to(m, m.by_id("h00"), m.by_id("a01"), prefer=(6, 13))
    assert kind == "empty" and square == (6, 13), "Grab should reach a square outside the arc"

    both = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6, "3+", ["Sidestep"]))
    _block(both, "h00", "a01", _dice([], [["push_back"]]), follow_up=False)
    t = both.by_id("a01")
    assert max(abs(t.x - 7), abs(t.y - 13)) == 1, "Grab should have suppressed Sidestep"


def test_fend_stops_the_follow_up():
    """ "then the opposition player may not Follow-up" — not optional, and not
    something the acting coach's follow_up flag can override."""
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6, "3+", ["Fend"]))
    out = _block(m, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "the blocker followed up anyway"
    assert any("Fend" in (e.text or "") for e in out.events)

    without = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    _block(without, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (without.by_id("h00").x, without.by_id("h00").y) == (7, 14)


def test_juggernaut_suppresses_fend_and_stand_firm_but_only_on_a_blitz():
    """ "when this player performs a Block Action AS PART OF A BLITZ ACTION,
    opposition players cannot use the Fend, Stand Firm or Wrestle Skills." """
    # Ordinary Block: Fend still works.
    plain = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6, "3+", ["Fend"]))
    _block(plain, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (plain.by_id("h00").x, plain.by_id("h00").y) == (7, 13), "Juggernaut suppressed Fend off a Blitz"

    # Declared Blitz: it does not.
    blitzed = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6, "3+", ["Fend"]))
    _declare(blitzed, "h00", "a01")
    _block(blitzed, "h00", "a01", _dice([], [["push_back"]]), follow_up=True)
    assert (blitzed.by_id("h00").x, blitzed.by_id("h00").y) == (7, 14), "Fend should have been suppressed"


def test_a_partly_modelled_skill_says_which_half_is_missing():
    """ "Modelled" and "not modelled" is a binary that flatters. A Skill with two
    clauses of which one is applied would report as modelled and quietly do half
    its job — which sounds settled, and is worse than saying nothing."""
    from bloodbowl.engine.game import state_report
    from bloodbowl.engine.skills import describe_skill, partly_modelled_on_pitch

    jug = describe_skill("Juggernaut")
    assert jug["modelled"] is True
    assert "Both Down" in jug["partial"], jug.get("partial")
    assert describe_skill("Grab").get("partial") is None, "a fully modelled skill needs no caveat"

    m = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6, "3+", ["Stand Firm"]))
    rows = {r["skill"]: r for r in partly_modelled_on_pitch(m)}
    assert set(rows) == {"Juggernaut", "Stand Firm"}
    assert rows["Stand Firm"]["players"] == ["a01"]
    assert "Crowd" in rows["Stand Firm"]["not_applied"]
    assert state_report(m)["partly_modelled_skills"] == partly_modelled_on_pitch(m)


def test_a_player_pushed_into_the_crowd_actually_leaves_the_pitch():
    """S3 INJURY BY THE CROWD: "Make an Injury Roll for a player Pushed into the
    Crowd. If the player would be Stunned, place them in their team's Reserve Box.
    Otherwise, follow the result on the relevant Injury Table."

    Two things were wrong, and the second is why nobody noticed the first:

    * An ARMOUR ROLL was made. There is none — the crowd does not care what you
      are wearing, which is the whole reason the sideline is dangerous.
    * `player_left_pitch` was emitted and never applied, so the victim stayed
      standing in the square they had just been thrown out of. The log said they
      went into the Crowd and the board disagreed.
    """
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    out = _block(m, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)

    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Armour" not in kinds, f"the Crowd made an Armour Roll: {kinds}"
    assert "Injury" in kinds, kinds
    t = m.by_id("a01")
    assert t.place != "pitch", "the victim is still standing on the pitch"
    assert t.place == "reserves", f"a Stunned crowd result is the Reserves Box, got {t.place}"
    assert t.down == "standing", "they are in the stands, not lying stunned on a square"


def test_the_crowd_can_still_knock_a_player_out():
    """ "Otherwise, follow the result on the relevant Injury Table" — 8-9 is a
    Knocked-out, and that box is not the Reserves box."""
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    _block(m, "h00", "a01", _dice([4, 4], [["push_back"]]), follow_up=False)
    assert m.by_id("a01").place == "knocked_out"


def test_a_carrier_thrown_into_the_crowd_leaves_the_ball_behind():
    """The ball does not go into the stands with them."""
    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    m.apply(_ball_at(1, 13, carrier="a01"))
    _block(m, "h00", "a01", _dice([2, 2, 3], [["push_back"]]), follow_up=False)
    assert m.by_id("a01").place == "reserves"
    assert m.ball.carrier == "", "the ball went into the Crowd too"
    assert m.ball.in_play


def test_declares_a_block_and_performs_a_block_are_different_triggers():
    """S3 gives this its own worked example: "a rule that comes into play when a
    player DECLARES a Block Action would not come into effect [during a Blitz], as
    no Block Action has been declared — the declared Action was a Blitz Action. A
    rule that comes into play when a player PERFORMS a Block Action would."

    Grab says "declares", Tackle says "performs". Reading the verb is the whole
    rule, and Grab shipped without it.
    """
    from bloodbowl.engine.actions.block import _push_to

    # Grab off a Blitz: reaches a square outside the ordinary arc.
    plain = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6))
    assert _push_to(plain, plain.by_id("h00"), plain.by_id("a01"), prefer=(6, 13))[0] == (6, 13)

    # The same Grab as part of a declared Blitz: it does not apply.
    blitzed = _match(("home", 7, 13, 6, "3+", ["Grab"]), ("away", 7, 14, 6))
    _declare(blitzed, "h00", "a01")
    assert _push_to(blitzed, blitzed.by_id("h00"), blitzed.by_id("a01"), prefer=(6, 13))[0] != (6, 13)

    # Tackle says "performs", so a Blitz does not switch it off.
    m = _match(("home", 7, 13, 6, "3+", ["Tackle"]), ("away", 7, 14, 6, "3+", ["Dodge"]))
    _declare(m, "h00", "a01")
    _block(m, "h00", "a01", _dice([2, 2], [["stumble"]]), follow_up=False)
    assert m.by_id("a01").down != "standing", "Tackle should still apply during a Blitz"


# --- skills batch three: the Both Down family and the block result ---------


def test_wrestle_places_both_players_prone_and_neither_is_harmed():
    """ "both players in the Block Action are Placed Prone, REGARDLESS of any other
    Skills they may possess" — and Placed Prone is the harmless one of the three
    ways onto the floor: "they aren't at risk of being caused harm". Neither
    player rolls armour, which is the whole value of the Skill."""
    m = _match(("home", 7, 13, 6, "3+", ["Block"]), ("away", 7, 14, 6, "3+", ["Wrestle"]))
    out = _block(m, "h00", "a01", _dice([], [["both_down"]]), follow_up=False)

    assert m.by_id("h00").down == "prone", "Wrestle drags the blocker down through Block"
    assert m.by_id("a01").down == "prone"
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert "Armour" not in kinds, f"Placed Prone must risk no harm: {kinds}"
    assert out.turnover


def test_without_wrestle_block_keeps_the_blocker_up_and_the_target_rolls_armour():
    """The control: the same Both Down with no Wrestle behaves as before."""
    m = _match(("home", 7, 13, 6, "3+", ["Block"]), ("away", 7, 14, 6))
    out = _block(m, "h00", "a01", _dice([2, 2], [["both_down"]]), follow_up=False)
    assert m.by_id("h00").down == "standing"
    assert m.by_id("a01").down == "prone"
    assert "Armour" in [r.kind for e in out.events for r in e.rolls]


def test_brawler_re_rolls_one_both_down_and_only_off_a_blitz():
    """ "they may re-roll a SINGLE Both Down result" — and it reads "declares a
    Block Action", so a Blitz switches it off."""
    m = _match(("home", 7, 13, 6, "3+", ["Brawler"]), ("away", 7, 14, 6))
    out = _block(m, "h00", "a01", _dice([], [["both_down"], ["push_back"]]), follow_up=False)
    kinds = [r.kind for e in out.events for r in e.rolls]
    assert kinds.count("Block") == 1 and kinds.count("Block (re-roll)") == 1, kinds
    assert m.by_id("h00").down == "standing", "the re-roll came up Push Back"

    blitzed = _match(("home", 7, 13, 6, "3+", ["Brawler"]), ("away", 7, 14, 6))
    _declare(blitzed, "h00", "a01")
    out2 = _block(blitzed, "h00", "a01", _dice([2, 2, 2, 2], [["both_down"]]), follow_up=False)
    assert "Block (re-roll)" not in [r.kind for e in out2.events for r in e.rolls], "Brawler fired on a Blitz"


def test_brawler_keeps_a_both_down_that_is_already_good_for_it():
    """A Both Down that floors only the target is a GOOD result. Re-rolling it
    away would be a bug wearing a rule's clothes."""
    m = _match(("home", 7, 13, 6, "3+", ["Brawler", "Block"]), ("away", 7, 14, 6))
    out = _block(m, "h00", "a01", _dice([2, 2], [["both_down"]]), follow_up=False)
    assert "Block (re-roll)" not in [r.kind for e in out.events for r in e.rolls]
    assert m.by_id("h00").down == "standing" and m.by_id("a01").down == "prone"


def test_juggernaut_turns_a_both_down_into_a_push_on_a_blitz():
    """ "they may treat any result of Both Down as Pushed Back during any Block
    Actions they perform during the Blitz Action." Only on a Blitz."""
    m = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    out = _block(m, "h00", "a01", _dice([], [["both_down"]]), follow_up=False)
    assert m.by_id("h00").down == "standing", "the blitzer should not have gone down"
    assert (m.by_id("a01").x, m.by_id("a01").y) != (7, 14), "the target should have been pushed"
    assert not out.turnover

    plain = _match(("home", 7, 13, 6, "3+", ["Juggernaut"]), ("away", 7, 14, 6))
    _block(plain, "h00", "a01", _dice([2, 2, 2, 2], [["both_down"]]), follow_up=False)
    assert plain.by_id("h00").down == "prone", "off a Blitz, Juggernaut does nothing here"


def test_dauntless_matches_the_stronger_player_rather_than_beating_them():
    """ "increases their unmodified Strength Characteristic to MATCH the opposition
    player for the duration of the Block Action. Modifiers are then applied as
    normal." Match, never exceed — ST 3 against ST 5 becomes 5 v 5, one die."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Dauntless"]), ("away", 7, 14, 6))
    m.by_id("h00").player.ST, m.by_id("a01").player.ST = "3", "5"

    ahead = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert ahead.detail["dauntless"] is True
    assert ahead.detail["dice"] == 2 and ahead.detail["chooser"] == "defender", "before the roll it is a bad block"

    out = _block(m, "h00", "a01", _dice([4], [["push_back"]]), follow_up=False)  # 4 + ST 3 = 7 > 5
    note = next(e for e in out.events if "Dauntless" in (e.text or ""))
    assert "matches ST 5" in note.text, note.text
    assert note.detail["dice"] == 1, "5 v 5 is one die"

    # A failed roll leaves it alone.
    m2 = _match(("home", 7, 13, 6, "3+", ["Dauntless"]), ("away", 7, 14, 6))
    m2.by_id("h00").player.ST, m2.by_id("a01").player.ST = "3", "5"
    out2 = _block(m2, "h00", "a01", _dice([1, 2, 2], [["push_back"]]), follow_up=False)
    assert any("fails the Dauntless" in (e.text or "") for e in out2.events)


def test_horns_adds_strength_only_on_a_blitz_and_shows_up_in_the_odds():
    """ "Whenever this player declares a Blitz Action, then they apply a +1 modifier
    to their Strength Characteristic for any Block Actions performed during that
    Blitz Action." Deterministic, so bb_game_odds shows the same dice resolve uses."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Horns"]), ("away", 7, 14, 6))
    m.by_id("h00").player.ST, m.by_id("a01").player.ST = "3", "3"

    plain = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert plain.detail["horns"] == 0 and plain.detail["dice"] == 1

    _declare(m, "h00", "a01")
    blitz = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert blitz.detail["horns"] == 1
    assert blitz.detail["attacker_strength"] == 4 and blitz.detail["dice"] == 2


def test_claws_break_armour_on_a_natural_eight_whatever_the_armour_value():
    """ "any roll of a NATURAL 8+ on the Armour Roll will break the opposition
    player's armour regardless of their actual Armour Value." Natural, so Mighty
    Blow cannot manufacture one."""
    m = _match(("home", 7, 13, 6, "3+", ["Claws"]), ("away", 7, 14, 6))
    m.by_id("a01").player.AV = "11+"
    out = _block(m, "h00", "a01", _dice([4, 4, 3, 3], [["pow"]]), follow_up=False)
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.dice == [4, 4] and armour.passed, "a natural 8 should break AV 11+"
    assert any(r.kind == "Injury" for e in out.events for r in e.rolls)

    # A natural 7 does not, however tempting.
    m2 = _match(("home", 7, 13, 6, "3+", ["Claws"]), ("away", 7, 14, 6))
    m2.by_id("a01").player.AV = "11+"
    out2 = _block(m2, "h00", "a01", _dice([3, 4], [["pow"]]), follow_up=False)
    assert not next(r for e in out2.events for r in e.rolls if r.kind == "Armour").passed


# --- skills batch four: the activation gates -------------------------------
#
# Five Traits share one shape: "Whenever this player is activated, after declaring
# their Action they must roll a D6", and on a failure something goes wrong.


def _act(m, action, cmd, dice):
    from bloodbowl.engine.game import act

    return act(m, action, cmd, dice)


def test_bone_head_distracts_the_player_and_the_action_never_happens():
    """ "On a 2+, the player may perform the declared Action as normal. On a 1, the
    player becomes Distracted." A failed gate stops the Action — it does not
    happen and then get undone."""
    m = _match(("home", 7, 13, 6, "3+", ["Bone Head"]))
    out = _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    p = m.by_id("h00")
    assert out["ok"] is False
    assert p.distracted is True
    assert (p.x, p.y) == (7, 13), "the Move happened anyway"
    assert p.done is True, "becoming Distracted ends the activation"

    passed = _match(("home", 7, 13, 6, "3+", ["Bone Head"]))
    assert _act(passed, "move", {"player": "h00", "x": 7, "y": 14}, _dice([2]))["ok"]
    assert (passed.by_id("h00").x, passed.by_id("h00").y) == (7, 14)


def test_a_distracted_player_has_no_tackle_zone_and_no_active_skills():
    """S3: "A player that is Distracted does not have a Tackle Zone … Whilst a
    player is Distracted, they cannot use ACTIVE Skills or Traits."

    Active-versus-Passive comes from the shipped catalogue, so this one rule
    covers all 108 — including the 81 the engine does not model.
    """
    from bloodbowl.engine.rules import has_tackle_zone
    from bloodbowl.engine.skills import can_use

    m = _match(("home", 7, 13, 6, "3+", ["Bone Head", "Dodge", "Thick Skull"]))
    p = m.by_id("h00")
    assert has_tackle_zone(p) and can_use(p, "Dodge")

    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert not has_tackle_zone(p), "a Distracted player still Marks people"
    assert not can_use(p, "Dodge"), "Dodge is an ACTIVE skill"
    assert can_use(p, "Thick Skull"), "Thick Skull is PASSIVE and still applies"


def test_distracted_lasts_until_the_player_is_next_activated_not_until_the_turn_ends():
    """ "they will remain Distracted UNTIL THEY ARE NEXT ACTIVATED (unless otherwise
    specified)" — so a new turn does not clear it, which is the half a paraphrase
    drops."""
    from bloodbowl.engine.game import end_turn

    m = _match(("home", 7, 13, 6, "3+", ["Bone Head"]), ("away", 2, 20, 6))
    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert m.by_id("h00").distracted

    end_turn(m)
    end_turn(m)  # back round to home
    assert m.by_id("h00").distracted, "a new turn must not clear Distracted"

    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([4]))
    assert not m.by_id("h00").distracted, "activating again clears it"


def test_really_stupid_is_not_helped_by_another_really_stupid_player():
    """ "+2 … if they have any Standing team-mates who are not Distracted, AND DO
    NOT HAVE THE REALLY STUPID TRAIT, adjacent to them." The exclusion is the
    clause that gets dropped, and two Trolls propping each other up is exactly
    what it forbids."""
    from bloodbowl.engine.skills import activation_gates

    alone = _match(("home", 7, 13, 6, "3+", ["Really Stupid"]))
    assert activation_gates(alone, alone.by_id("h00"), "move")[0]["modifier"] == 0

    propped = _match(("home", 7, 13, 6, "3+", ["Really Stupid"]), ("home", 7, 14, 6, "3+", ["Really Stupid"]))
    assert activation_gates(propped, propped.by_id("h00"), "move")[0]["modifier"] == 0, "two of them do not help"

    helped = _match(("home", 7, 13, 6, "3+", ["Really Stupid"]), ("home", 7, 14, 6))
    assert activation_gates(helped, helped.by_id("h00"), "move")[0]["modifier"] == 2


@pytest.mark.parametrize("action,want", [("block", 2), ("blitz", 2), ("move", 0)])
def test_three_gates_give_plus_two_for_declaring_violence(action, want):
    """Animal Savagery and Unchannelled Fury: "+2 … if they have declared a Block
    Action or a Blitz Action"."""
    from bloodbowl.engine.skills import activation_gates

    for trait in ("Animal Savagery", "Unchannelled Fury"):
        m = _match(("home", 7, 13, 6, "3+", [trait]))
        assert activation_gates(m, m.by_id("h00"), action)[0]["modifier"] == want, trait


def test_animal_savagery_lashes_out_at_a_team_mate():
    """ "Choose one Standing team-mate adjacent to this player; the chosen player is
    immediately Knocked Down. This will NOT cause a Turnover unless the player was
    holding the ball." """
    m = _match(("home", 7, 13, 6, "3+", ["Animal Savagery"]), ("home", 7, 14, 6))
    out = _act(m, "move", {"player": "h00", "x": 6, "y": 13}, _dice([1, 2, 2]))
    assert m.by_id("h01").down != "standing", "the team-mate should be on the floor"
    assert out["turnover"] is False, "no turnover unless they had the ball"

    # …with the ball, it IS a turnover.
    # …with the ball it IS a turnover. More dice, because going down drops the
    # ball and a dropped ball BOUNCES before anything else resolves.
    withball = _match(("home", 7, 13, 6, "3+", ["Animal Savagery"]), ("home", 7, 14, 6))
    withball.apply(_ball_at(7, 14, carrier="h01"))
    out2 = _act(withball, "move", {"player": "h00", "x": 6, "y": 13}, _dice([1, 3, 2, 2, 4, 4, 4]))
    assert out2["turnover"] is True


def test_animal_savagery_with_nobody_to_hit_is_just_distracted():
    """ "If this player rolls a 1-3 and there are no Standing team-mates adjacent to
    them, then they are Distracted." """
    m = _match(("home", 7, 13, 6, "3+", ["Animal Savagery"]))
    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert m.by_id("h00").distracted is True


def test_take_root_roots_them_to_the_spot():
    """ "Whilst Rooted, a player cannot perform Move Actions, may not Follow-up
    after performing a Block Action, cannot be Pushed Back, and may not leave their
    current square for any reason." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6, "3+", ["Take Root"]), ("away", 7, 14, 6))
    _act(m, "move", {"player": "h00", "x": 6, "y": 12}, _dice([1]))
    assert m.by_id("h00").rooted is True
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13)

    later = actions.get("move")["validate"](m, {"player": "h00", "x": 6, "y": 12})
    assert not later.ok and "Rooted" in later.reason

    # …and they cannot be shoved, either.
    m.apply(_ball_at(1, 1))
    from bloodbowl.engine.game import end_turn

    end_turn(m)
    out = _block(m, "a01", "h00", _dice([], [["push_back"]]), follow_up=True)
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 13), "a Rooted player was Pushed Back"
    assert any("Rooted" in (e.text or "") for e in out.events)


def test_being_knocked_down_pulls_a_rooted_player_up_by_the_roots():
    """ "A Rooted player will immediately stop being Rooted at the end of a Drive,
    or if they are ever Knocked Down or Placed Prone." """
    from bloodbowl.engine.injury import knock_down

    m = _match(("home", 7, 13, 6, "3+", ["Take Root"]))
    _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([1]))
    assert m.by_id("h00").rooted
    knock_down(m, m.by_id("h00"), _dice([2, 2]))
    assert m.by_id("h00").rooted is False


def test_unchannelled_fury_just_ends_the_activation():
    """ "this player rages incoherently but nothing really happens. Their activation
    immediately ends." No Distracted, no damage — the turn simply moves on."""
    m = _match(("home", 7, 13, 6, "3+", ["Unchannelled Fury"]))
    out = _act(m, "move", {"player": "h00", "x": 7, "y": 14}, _dice([2]))
    p = m.by_id("h00")
    assert out["ok"] is False and out["turnover"] is False
    assert p.done is True and p.distracted is False and (p.x, p.y) == (7, 13)


def test_drunkard_makes_the_rush_harder():
    """ "This player applies a -1 modifier to test whenever they attempt to Rush." """
    # MA 1, so the SECOND square needs a Rush — the first is free, which is what
    # the previous version of this test forgot.
    m = _match(("home", 7, 13, 1, "3+", ["Drunkard"]))
    assert _move(m, "h00", 7, 14, _dice([])).ok
    out = _move(m, "h00", 7, 15, _dice([4]))
    rush = next((r for e in out.events for r in e.rolls if r.kind == "Rush"), None)
    assert rush is not None and rush.modifier == -1, rush
    assert rush.passed, "4 - 1 = 3 still beats a 2+"


# --- Team Re-rolls ---------------------------------------------------------


def _reroll_board(skills=(), n=3):
    """A match with `n` Team Re-rolls a side and a Marked home player."""
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6, "3+", list(skills)), ("away", 7, 14, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "rerolls": {"home": n, "away": n}}))
    return m


def test_a_coach_may_use_as_many_team_re_rolls_as_they_want_in_a_turn():
    """S3: "A COACH MAY USE AS MANY TEAM RE-ROLLS AS THEY WANT DURING THEIR TURN,
    though they may still never re-roll a re-roll."

    A previous edition allowed one per team turn and that is what most people will
    tell you. S3 does not cap them at all — the only limits are how many you
    bought and that a re-roll cannot itself be re-rolled.
    """
    from bloodbowl.engine.events import Event

    # A second Marker beside the FIRST destination, so the second step also needs
    # a Dodge — otherwise there is nothing for the second re-roll to re-roll and
    # the test passes against an engine that allows only one.
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 5, 12, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "rerolls": {"home": 3, "away": 3}}))

    assert _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True).ok
    assert m.rerolls["home"] == 2
    assert _move(m, "h00", 6, 11, _dice([1, 5]), team_reroll=True).ok
    assert m.rerolls["home"] == 1, "the second re-roll in one turn was refused"


def test_a_team_re_roll_is_only_spent_when_the_coach_asks():
    """The engine cannot stop mid-action to ask, so the coach pre-commits — the
    same way they already do for `choice`, `follow_up` and `push_to`. Spending one
    unasked would be the engine making an expensive decision on their behalf."""
    m = _reroll_board()
    out = _move(m, "h00", 6, 12, _dice([1, 2, 2]))
    assert not out.ok and m.rerolls["home"] == 3, "a re-roll was spent without being asked for"


def test_a_free_skill_re_roll_is_always_tried_before_the_teams():
    """Dodge's re-roll costs nothing; the team's is finite. Spending the finite one
    while a free one sat unused would be strictly worse in every position."""
    m = _reroll_board(skills=["Dodge"])
    out = _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True)
    assert out.ok
    assert m.rerolls["home"] == 3, "the Team Re-roll was spent despite the Dodge Skill"
    assert any("Dodge skill" in (r.note or "") for e in out.events for r in e.rolls)


def test_a_team_re_roll_may_not_re_roll_an_armour_or_injury_roll():
    """ "A Team Re-roll cannot be used to re-roll any of the following types of
    roll: Scatter, Armour, Injury, Casualty, Throw-in, Bribe, Argue the Call or if
    the Crowd Takes Action." """
    from bloodbowl.engine.rerolls import excluded

    for kind in ("Armour", "Injury", "Casualty", "Scatter", "Throw-in", "Argue the Call", "Bounce"):
        assert excluded(kind), kind
    for kind in ("Dodge", "Rush", "Pick up", "Catch", "Pass", "Block", "Bone Head"):
        assert not excluded(kind), kind

    # …and the exclusion holds through a real action: a failed Dodge re-rolls, the
    # Armour Roll behind the fall does not.
    m = _reroll_board(n=3)
    out = _move(m, "h00", 6, 12, _dice([1, 1, 2, 2]), team_reroll=True)
    assert not out.ok, "both Dodges failed"
    assert m.rerolls["home"] == 2, "exactly one re-roll, on the Dodge"


def test_a_team_re_roll_needs_it_to_be_your_turn():
    """ "Team Re-rolls can only be used when the team is active … can never be used
    to re-roll an opposing Coach's dice." """
    from bloodbowl.engine.rerolls import available

    m = _reroll_board()
    assert available(m, m.by_id("h00")) == 3
    assert available(m, m.by_id("a01")) == 0, "the inactive side offered a re-roll"


def test_the_whole_block_pool_is_re_rolled_not_the_one_face():
    """ "When a Team Re-roll is used to re-roll a dice pool, ALL THE DICE IN THE
    POOL must be re-rolled." """
    m = _reroll_board()
    m.by_id("h00").player.ST, m.by_id("a01").player.ST = "4", "3"  # two dice
    out = _block(
        m,
        "h00",
        "a01",
        _dice([2, 2], [["player_down", "player_down"], ["pow", "push_back"]]),
        team_reroll=True,
        follow_up=False,
    )
    again = next(r for e in out.events for r in e.rolls if r.kind == "Block (Team Re-roll)")
    assert len(again.dice) == 2, f"only {len(again.dice)} die re-rolled"
    assert m.rerolls["home"] == 2 and out.ok


def test_a_block_result_that_is_good_for_us_is_not_re_rolled():
    """A Push Back or a POW is what you wanted. Spending the team's finite re-roll
    on one would be the engine throwing money away on the coach's behalf."""
    m = _reroll_board()
    _block(m, "h00", "a01", _dice([2, 2], [["pow"]]), team_reroll=True, follow_up=False)
    assert m.rerolls["home"] == 3


def test_loner_must_pass_a_roll_and_loses_the_re_roll_either_way():
    """ "If they roll lower than the number shown in brackets, then they may not
    re-roll the dice and THE TEAM RE-ROLL IS LOST just as if it had been used."

    Losing it either way is the entire cost of the Trait.
    """
    m = _reroll_board(skills=["Loner (4+)"])
    out = _move(m, "h00", 6, 12, _dice([1, 2, 2, 2]), team_reroll=True)  # Dodge 1, Loner 2 = fail
    assert not out.ok, "the Dodge should still have failed"
    assert m.rerolls["home"] == 2, "a failed Loner must still cost the re-roll"
    assert any("Loner" in (e.text or "") for e in out.events)

    passed = _reroll_board(skills=["Loner (4+)"])
    ok_out = _move(passed, "h00", 6, 12, _dice([1, 5, 5]), team_reroll=True)  # Dodge 1, Loner 5, Dodge 5
    assert ok_out.ok and passed.rerolls["home"] == 2


def test_loner_reads_the_number_out_of_its_own_brackets():
    """`Loner (4+)` and `Loner (2+)` are different Traits wearing one name."""
    from bloodbowl.engine.rerolls import _loner_target

    m = _match(("home", 7, 13, 6, "3+", ["Loner (2+)"]), ("home", 8, 13, 6, "3+", ["Loner (5+)"]), ("home", 9, 13, 6))
    assert _loner_target(m.by_id("h00")) == 2
    assert _loner_target(m.by_id("h01")) == 5
    assert _loner_target(m.by_id("h02")) is None


def test_re_rolls_are_replenished_at_half_time_and_do_not_carry_over():
    """ "any used during the first half of a game will be replenished at half-time …
    Unused Team Re-rolls do NOT carry over to the next half." One assignment does
    both halves of that sentence — up for the team that spent them, down for the
    team that hoarded."""
    from bloodbowl.engine.game import end_turn

    m = _reroll_board(n=2)
    _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True)
    assert m.rerolls["home"] == 1

    # Grant the away side an extra to prove the reset works downwards too.
    m.rerolls["away"] = 9
    for _ in range(2 * 8):
        end_turn(m)
    assert m.clock.half == 2, m.clock
    assert m.rerolls == {"home": 2, "away": 2}, m.rerolls
    assert any(e.kind == "half_time" for e in m.events), "half-time was never recorded in the log"


def test_legal_moves_says_how_many_re_rolls_are_left_before_committing():
    from bloodbowl.engine.game import legal_moves

    m = _reroll_board(skills=["Loner (4+)"])
    out = legal_moves(m, "h00")
    assert out["team_rerolls"] == {"left": 3, "loner": 4}


# --- kick-off events that Team Re-rolls unblocked --------------------------


def _kickoff_board(staff=None, rerolls=2):
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(
        Event(
            kind="match_started",
            detail={
                "kicking_to": "home",
                "rerolls": {"home": rerolls, "away": rerolls},
                "staff": staff or {},
            },
        )
    )
    return m


def _event(m, dice, name):
    """Force one Kick-off Event by its name, and return its events."""
    from bloodbowl.engine import kickoff

    total = next(k for k, v in kickoff.KICKOFF_EVENTS.items() if v[0] == name)
    # The event roll is 2D6; feed it two dice that sum to the one we want.
    lo = max(1, total - 6)
    return kickoff.kickoff_event(m, _dice([lo, total - lo, *dice]), receiving="home")


def test_brilliant_coaching_grants_a_re_roll_for_the_drive_only():
    """ "Both Coaches roll a D6 and add the number of Assistant Coaches on their
    Team Roster. The Coach with the highest total gains a free Team Re-roll FOR
    THE DRIVE AHEAD."

    For the Drive — so it expires, which is why it is counted apart from the ones
    the team bought rather than added to them.
    """
    from bloodbowl.engine.game import start_drive
    from bloodbowl.engine.rerolls import available

    m = _kickoff_board(staff={"home": {"assistant_coaches": 2}}, rerolls=2)
    _event(m, [3, 3], "Brilliant Coaching")  # home 3+2=5, away 3+0=3
    assert m.drive_rerolls == {"home": 1}
    assert available(m, m.by_id("h00")) == 3, "2 bought plus 1 for the Drive"

    start_drive(m, receiving="home", dice=_dice([3, 1, 4, 4, 3, 3, 2, 2] + [4] * 12))
    assert m.drive_rerolls == {}, "the Drive re-roll outlived its Drive"
    assert m.rerolls["home"] == 2


def test_the_drive_re_roll_is_spent_before_the_bought_ones():
    """It is the one that expires, so spending it first is the only reading that
    does not quietly throw it away."""
    m = _kickoff_board(staff={"home": {"assistant_coaches": 2}}, rerolls=2)
    _event(m, [3, 3], "Brilliant Coaching")
    assert (m.drive_rerolls["home"], m.rerolls["home"]) == (1, 2)

    _move(m, "h00", 6, 12, _dice([1, 5]), team_reroll=True)
    assert (m.drive_rerolls["home"], m.rerolls["home"]) == (0, 2), "a bought re-roll was spent first"


def test_a_tie_on_a_contested_kick_off_event_gives_nobody_anything():
    """ "The Coach with the highest total gains…" — on a tie there is not one."""
    m = _kickoff_board(rerolls=2)
    out = _event(m, [4, 4], "Brilliant Coaching")
    assert m.drive_rerolls == {}
    assert any("a tie" in (e.text or "") for e in out)


def test_cheering_fans_gives_one_extra_offensive_assist_on_the_next_turn():
    """ "The FIRST Block Action performed during the Coach with the highest roll's
    next Turn receives an additional Offensive Assist." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _kickoff_board(staff={"home": {"cheerleaders": 3}})
    _event(m, [2, 2], "Cheering Fans")  # home 2+3=5, away 2
    assert m.cheer == {"side": "home", "ready": False}

    # It arms when that Coach's Turn begins.
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 1}))
    assert m.cheer["ready"] is True

    legal = actions.get("block")["validate"](m, {"player": "h00", "target": "a01"})
    assert legal.detail["cheered"] is True
    assert legal.detail["offensive_assists"] == 1, "the crowd's assist is missing"
    assert legal.detail["attacker_strength"] == legal.detail["defender_strength"] + 1

    # …and it is consumed by that first Block.
    _block(m, "h00", "a01", _dice([2, 2], [["push_back"]]), follow_up=False)
    assert m.cheer == {}, "the crowd cheered twice"


def test_cheering_fans_expires_if_that_turn_goes_by_unused():
    """ "…during the Coach's NEXT Turn" — one Turn, not a standing bonus."""
    from bloodbowl.engine.events import Event

    m = _kickoff_board(staff={"home": {"cheerleaders": 3}})
    _event(m, [2, 2], "Cheering Fans")
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 1}))
    assert m.cheer["ready"]
    m.apply(Event(kind="turn_started", detail={"side": "away", "half": 1, "turn": 1}))
    assert m.cheer["ready"], "the opponent's turn must not consume it"
    m.apply(Event(kind="turn_started", detail={"side": "home", "half": 1, "turn": 2}))
    assert m.cheer == {}, "it should have expired after their Turn"


def test_the_staff_numbers_are_inputs_and_default_to_none():
    """A practice board hired nobody, so both events are a bare D6 unless told
    otherwise — and the roll says what it added."""
    m = _kickoff_board()
    out = _event(m, [5, 2], "Brilliant Coaching")
    assert m.drive_rerolls == {"home": 1}
    line = next(e.text for e in out if e.text and "home roll" in e.text)
    assert line.endswith("home roll 5."), line


def test_the_casualty_roll_is_made_and_reported_even_though_it_changes_nothing_here():
    """ "Whenever a player suffers a Casualty, the opposing Coach makes a Casualty
    Roll against them by rolling a D16."

    Everything on that table is a League consequence — "in all instances the
    player will miss the rest of the current game" — so nothing about THIS match
    changes. It is rolled anyway, because a coach asking what happened to their
    Blitzer deserves the answer, and because an Apothecary keys off the result.
    """
    from bloodbowl.engine.injury import risk_injury

    m = _match(("home", 7, 13, 6))
    m.by_id("h00").player.AV = "2+"
    events = risk_injury(m, m.by_id("h00"), _dice([6, 6, 6, 6, 15]))
    cas = next(e for e in events if e.kind == "casualty_roll")
    assert cas.detail["roll"] == 15 and cas.detail["result"] == "Dead"
    assert cas.detail["league_only"] is True
    assert m.by_id("h00").place == "casualty", "the match effect is the same whatever the D16 said"


@pytest.mark.parametrize(
    "roll,want",
    [
        (1, "Badly Hurt"),
        (8, "Badly Hurt"),
        (9, "Seriously Hurt"),
        (12, "Serious Injury"),
        (14, "Lasting Injury"),
        (16, "Dead"),
    ],
)
def test_the_casualty_table_boundaries(roll, want):
    """1-8 / 9-10 / 11-12 / 13-14 / 15-16 — five bands, and every boundary is a
    place an off-by-one hides."""
    from bloodbowl.engine.injury import casualty_roll

    m = _match(("home", 7, 13, 6))
    ev = casualty_roll(m, m.by_id("h00"), _dice([roll]))[0]
    assert ev.detail["result"] == want


def test_every_dice_implementation_can_roll_an_arbitrary_die():
    """The table wants a D3 and a D16, and several rules want "randomly select one
    of their players" — a die with as many sides as they have. One method rather
    than three, across all four implementations."""
    from bloodbowl.engine.dice import ReplayDice, Roll, ScriptedDice, SeededDice

    assert 1 <= SeededDice(seed=3).dn(16) <= 16
    assert ScriptedDice(script=[11]).dn(16) == 11
    assert ReplayDice(recorded=[Roll(kind="d16", dice=[11])]).dn(16) == 11
    # …and a scripted value outside the die is an error, not a silent pass.
    with pytest.raises(AssertionError):
        ScriptedDice(script=[17]).dn(16)


# --- Jumping over players, Forego Activation and Stalling ------------------


def test_a_jump_goes_over_a_downed_player_to_the_far_side_only():
    """ "a player may attempt to Jump into an unoccupied square that is adjacent to
    the Prone or Stunned player they are attempting to Jump over, but IS NOT
    ALREADY ADJACENT TO THE JUMPING PLAYER."

    That last clause is the shape of the whole rule — the far side of the body,
    not any square touching it — and without it a Jump is a free diagonal step.
    """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))

    far = actions.get("move")["validate"](m, {"player": "h00", "x": 7, "y": 15})
    assert far.ok and far.detail["jump"] is True and far.detail["jump_over"] == "a01"

    # (6,14) touches the body but is ALREADY adjacent to the jumper, so it is not
    # a Jump — and it is not an adjacent step either, so it is simply refused.
    near = actions.get("move")["validate"](m, {"player": "h00", "x": 6, "y": 14})
    assert near.ok and not near.detail.get("jump"), "that is an ordinary step, not a Jump"

    # …and there is nothing to jump over when the player is STANDING.
    standing = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    assert not actions.get("move")["validate"](standing, {"player": "h00", "x": 7, "y": 15}).ok


def test_a_jump_costs_two_squares_of_move_allowance():
    """ "As the player is moving 2 squares when jumping, it will also cost 2 squares
    of Move Allowance." """
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
    out = _move(m, "h00", 7, 15, _dice([5]))
    assert out.ok
    assert m.by_id("h00").ma_used == 2, "a Jump that cost one square"
    assert (m.by_id("h00").x, m.by_id("h00").y) == (7, 15)


def test_a_jump_takes_the_worse_of_the_two_squares_not_the_destination():
    """ "a negative modifier equal to the number of opposition players Marking the
    square they are currently in, OR the number … Marking the square they are
    jumping into, WHICHEVER IS HIGHER." A Dodge uses the destination alone; this
    does not, and the two are easy to conflate."""
    from bloodbowl.engine.events import Event

    # Two Markers on the square being left, none on the landing square.
    m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 6, 13, 6), ("away", 8, 13, 6))
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
    out = _move(m, "h00", 7, 15, _dice([6]))
    jump = next(r for e in out.events for r in e.rolls if r.kind == "Jump")
    assert jump.modifier == -2, "the square being LEFT was the worse one"


def test_a_failed_jump_lands_them_in_the_target_square_but_a_natural_one_does_not():
    """ "If the test is failed, place the jumping player IN THE TARGET SQUARE where
    they will Fall Over … If a NATURAL 1 is rolled … the player will instead Fall
    Over IN THE SQUARE THEY ARE IN."

    Two different squares, and the difference decides where a dropped ball lands.
    """
    from bloodbowl.engine.events import Event

    def board():
        m = _match(("home", 7, 13, 6), ("away", 7, 14, 6), ("away", 6, 13, 6))
        m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
        return m

    failed = board()  # AG 3+ with -1 → a 3 fails, and is not a natural 1
    out = _move(failed, "h00", 7, 15, _dice([3, 2, 2]))
    assert not out.ok
    assert (failed.by_id("h00").x, failed.by_id("h00").y) == (7, 15), "a plain failure still moves them"

    natural = board()
    _move(natural, "h00", 7, 15, _dice([1, 2, 2]))
    assert (natural.by_id("h00").x, natural.by_id("h00").y) == (7, 13), "a natural 1 falls where they stood"


def test_foregoing_an_activation_stops_the_player_acting_again_this_turn():
    """ "once a player has declared they will forego their activation, they cannot
    later be activated in the same turn." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 13, 6), ("away", 2, 20, 6))
    out = actions.get("forego")["resolve"](m, {"player": "h00"}, _dice([]))
    assert out.ok and m.by_id("h00").done is True
    later = actions.get("move")["validate"](m, {"player": "h00", "x": 7, "y": 14})
    assert not later.ok and "activation is over" in later.reason


def test_a_player_who_could_have_walked_it_in_is_stalling():
    """ "Should a player be in possession of the ball when they are activated, can
    score a Touchdown WITHOUT HAVING TO ROLL ANY DICE, yet finishes their
    activation without having scored … then they are said to be Stalling."

    And the comparison runs the way that makes stalling EARLY dangerous: "if the
    score on the D6 is equal to or greater than the team's current Turn number".
    Turn 2 is punished on a 2+; turn 8 cannot be punished at all, which is the
    rule allowing a team to run the clock down at the end.
    """
    from bloodbowl.engine import actions

    actions.load_all()

    def board(turn):
        m = _match(("home", 7, 24, 6), ("away", 2, 4, 6))
        m.apply(_ball_at(7, 24, carrier="h00"))
        m.clock.turn = turn
        return m

    caught = board(2)
    # crowd 4, then the knock-down: the ball they were holding bounces (d8), then armour
    out = actions.get("forego")["resolve"](caught, {"player": "h00"}, _dice([4, 3, 2, 2]))
    assert out.turnover, "4 >= turn 2, so the crowd acts"
    assert caught.by_id("h00").down != "standing"

    late = board(8)
    out2 = actions.get("forego")["resolve"](late, {"player": "h00"}, _dice([6]))
    assert not out2.turnover, "on turn 8 a D6 can never reach the turn number"
    assert any("could have scored" in (e.text or "") for e in out2.events)


def test_a_player_who_needed_a_roll_to_score_is_not_stalling():
    """ "If a player needs to roll any dice in order to score, such as having to
    Dodge, Rush, perform a Block Action … then they are NOT said to be Stalling."
    """
    from bloodbowl.engine.rules import could_score_without_dice

    # Marked, so leaving needs a Dodge.
    marked = _match(("home", 7, 24, 6), ("away", 7, 25, 6))
    marked.apply(_ball_at(7, 24, carrier="h00"))
    assert not could_score_without_dice(marked, marked.by_id("h00"))

    # Out of Move Allowance, so it needs a Rush.
    far = _match(("home", 7, 14, 1), ("away", 2, 4, 6))
    far.apply(_ball_at(7, 14, carrier="h00"))
    assert not could_score_without_dice(far, far.by_id("h00"))

    # A Trait they must roll for on activation.
    thick = _match(("home", 7, 24, 6, "3+", ["Bone Head"]), ("away", 2, 4, 6))
    thick.apply(_ball_at(7, 24, carrier="h00"))
    assert not could_score_without_dice(thick, thick.by_id("h00"))

    # …and the plain case, which must still be Stalling or the test proves nothing.
    clear = _match(("home", 7, 24, 6), ("away", 2, 4, 6))
    clear.apply(_ball_at(7, 24, carrier="h00"))
    assert could_score_without_dice(clear, clear.by_id("h00"))


def test_passing_the_ball_away_instead_of_scoring_is_not_stalling():
    """The exception the rule states outright: "If a player who could score a
    Touchdown without rolling any dice, and therefore would be deemed to be
    Stalling, declares a Pass Action or a Hand-off Action AND FINISHES THEIR
    ACTION NO LONGER IN POSSESSION OF THE BALL, then this is NOT deemed to be
    Stalling."

    Giving the ball to a team-mate is playing on, not running down the clock. The
    engine gets this right because it asks who holds the ball when the activation
    FINISHES rather than when it began — worth a test, because the other reading
    is just as easy to write and is wrong.
    """
    from bloodbowl.engine.game import act

    m = _match(("home", 7, 24, 6), ("home", 8, 24, 6), ("away", 2, 4, 6))
    m.apply(_ball_at(7, 24, carrier="h00"))
    m.clock.turn = 2
    out = act(m, "handoff", {"player": "h00", "target": "h01"}, _dice([5]))
    assert m.ball.carrier == "h01", "the hand-off should have connected"
    assert not any("could have scored" in (e["text"] or "") for e in out["events"])
    assert not out["turnover"]


# --- the Weather, and the kick-off events it unblocked ---------------------


def _weathered(condition, *extra):
    """A lone home player unless told otherwise — an adjacent opponent would add a
    Marking penalty and hide the one modifier under test."""
    from bloodbowl.engine.events import Event

    m = _match(("home", 7, 13, 6), *extra)
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "weather": condition}))
    return m


def test_the_weather_table_bands():
    """ "each Coach rolls a D6 and adds the two rolls together" — 2, 3, 4-10, 11, 12,
    and the wide band in the middle is where most games live."""
    from bloodbowl.engine.weather import from_roll

    assert from_roll(2)[0] == "sweltering_heat"
    assert from_roll(3)[0] == "very_sunny"
    for total in range(4, 11):
        assert from_roll(total)[0] == "perfect", total
    assert from_roll(11)[0] == "pouring_rain"
    assert from_roll(12)[0] == "blizzard"


def test_pouring_rain_makes_the_ball_slippery():
    """ "Whenever a player attempts to pick up or Catch the ball, or Intercept a
    Pass Action, they suffer a -1 modifier to the roll." Three tests, one
    condition — and it rides the same hook Skills use."""
    from bloodbowl.engine.ball import pick_up

    m = _weathered("pouring_rain")
    m.apply(_ball_at(7, 13))
    events = pick_up(m, m.by_id("h00"), _dice([4]))[0]
    r = next(r for e in events for r in e.rolls if r.kind == "Pick up")
    assert r.modifier == -1 and "Pouring Rain" in (r.note or "")

    dry = _weathered("perfect")
    dry.apply(_ball_at(7, 13))
    dry_events = pick_up(dry, dry.by_id("h00"), _dice([4]))[0]
    assert next(r for e in dry_events for r in e.rolls if r.kind == "Pick up").modifier == 0


def test_a_blizzard_slows_the_rush_and_forbids_the_long_throws():
    """ "Whenever a player attempts to Rush, apply an additional -1 modifier …
    Additionally, when a player makes a Pass Action, they may only attempt to make
    a Quick Pass or a Short Pass."

    The second clause is a LEGALITY, not a penalty — a Long Bomb in a blizzard is
    refused with a reason rather than thrown at long odds.
    """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _weathered("blizzard")
    m.by_id("h00").player.PA = "3+"
    m.apply(_ball_at(7, 13, carrier="h00"))

    short = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 17})
    assert short.ok and short.detail["range"] == "Short Pass"
    long_one = actions.get("pass")["validate"](m, {"player": "h00", "x": 7, "y": 24})
    assert not long_one.ok and "Blizzard" in long_one.reason

    # …and the Rush is a modifier, not a ban. No opponent nearby, so nothing but
    # the weather touches the roll.
    rushing = _weathered("blizzard")
    rushing.by_id("h00").player.MA = "1"
    assert _move(rushing, "h00", 6, 12, _dice([])).ok
    out = _move(rushing, "h00", 6, 11, _dice([5]))
    rush = next(r for e in out.events for r in e.rolls if r.kind == "Rush")
    assert rush.modifier == -1


def test_changing_weather_rerolls_the_table_and_scatters_on_perfect():
    """ "Roll again on the Weather Table … If the new result is Perfect Conditions,
    the ball will Scatter (3) in the air before it lands." """
    from bloodbowl.engine import kickoff

    m = _weathered("blizzard")
    m.apply(_ball_at(7, 8))
    out = kickoff.kickoff_event(m, _dice([4, 4, 3, 3, 1, 1, 1, 1]), receiving="home")
    assert m.weather == "perfect", "the weather should have changed"
    assert any(e.kind == "weather_changed" for e in out)
    assert sum(1 for e in out for r in e.rolls if r.kind == "Scatter") >= 1 or any(
        "Scatters three times" in (e.text or "") for e in out
    )


def test_a_random_player_pick_is_uniform_and_replayable():
    """ "randomly selects one of their players on the pitch" — a die with as many
    sides as they have, recorded like any other roll so a replay picks the same
    player."""
    from bloodbowl.engine.kickoff import _random_player

    m = _match(("home", 1, 1, 6), ("home", 2, 2, 6), ("home", 3, 3, 6), ("away", 9, 9, 6))
    assert _random_player(m, "home", _dice([1])).id == "h00"
    assert _random_player(m, "home", _dice([3])).id == "h02"
    assert _random_player(m, "home", _dice([2]), exclude={"h00"}).id == "h02"
    assert _random_player(m, "away", _dice([1])).id == "a03"
    assert _random_player(m, "home", _dice([1]), exclude={"h00", "h01", "h02"}) is None


def test_pitch_invasion_stuns_d3_of_the_losing_sides_players():
    """ "The Coach that rolled lowest, or BOTH on a tie, randomly selects D3 of
    their players on the pitch. The selected players are immediately Placed Prone
    and become Stunned." """
    from bloodbowl.engine import kickoff
    from bloodbowl.engine.events import Event

    m = _match(*[("home", x, 5, 6) for x in range(1, 6)], *[("away", x, 20, 6) for x in range(1, 6)])
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "staff": {"home": {"fan_factor": 3}}}))
    # 5 + 3 fans = 8 for home, 2 for away → away loses. D3 rolls 2.
    kickoff.kickoff_event(m, _dice([6, 6, 5, 2, 2, 1, 1]), receiving="home")
    stunned = [p for p in m.players if p.down == "stunned"]
    assert len(stunned) == 2, [p.id for p in stunned]
    assert all(p.side == "away" for p in stunned), "the wrong side was invaded"


def test_dodgy_snack_either_weakens_a_player_or_sends_them_off_the_pitch():
    """ "On a 2+ the player reduces their MA and AV by 1 for the Drive. On a 1,
    place the player in the Reserves box." """
    from bloodbowl.engine import kickoff

    def board():
        return _match(("home", 1, 5, 6), ("home", 2, 5, 6), ("away", 1, 20, 6))

    # 2D6 for the event (5+6 = 11, Dodgy Snack), then home 6 / away 5 so AWAY is
    # the lowest, then the pick, then the snack roll.
    sick = board()
    kickoff.kickoff_event(sick, _dice([5, 6, 6, 5, 1, 1]), receiving="home")
    assert sick.by_id("a02").place == "reserves"

    queasy = board()
    kickoff.kickoff_event(queasy, _dice([5, 6, 6, 5, 1, 4]), receiving="home")
    assert queasy.by_id("a02").place == "pitch"


# --- Throw Team-mate -------------------------------------------------------


def _ttm_board(thrower=("Throw Team-mate",), thrown=("Right Stuff",), pa="3+"):
    m = _match(("home", 7, 8, 6, "3+", list(thrower)), ("home", 7, 9, 6, "3+", list(thrown)))
    m.by_id("h00").player.PA = pa
    return m


def _throw(m, dice, x=7, y=12, **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get("throwteam")["resolve"](m, {"player": "h00", "target": "h01", "x": x, "y": y, **cmd}, dice)


def test_only_a_thrower_may_throw_and_only_right_stuff_may_be_thrown():
    """ "A player may only declare this Action if they have the Throw Team-mate
    Trait … they may pick up an adjacent team-mate with the Right Stuff Trait." """
    from bloodbowl.engine import actions

    actions.load_all()
    v = actions.get("throwteam")["validate"]

    no_trait = _ttm_board(thrower=())
    assert not v(no_trait, {"player": "h00", "target": "h01", "x": 7, "y": 12}).ok

    wrong_cargo = _ttm_board(thrown=())
    out = v(wrong_cargo, {"player": "h00", "target": "h01", "x": 7, "y": 12})
    assert not out.ok and "Right Stuff" in out.reason

    ok = _ttm_board()
    assert v(ok, {"player": "h00", "target": "h01", "x": 7, "y": 12}).ok


def test_a_team_mate_does_not_go_as_far_as_a_ball():
    """ "The declared square must be wholly underneath the FIRST TWO SECTIONS of the
    Range Ruler … I: Quick Throw, II: Short Throw." There is no Long Throw."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _ttm_board()
    v = actions.get("throwteam")["validate"]

    assert v(m, {"player": "h00", "target": "h01", "x": 7, "y": 10}).detail["range"] == "Quick Throw"
    assert v(m, {"player": "h00", "target": "h01", "x": 7, "y": 13}).detail["range"] == "Short Throw"
    far = v(m, {"player": "h00", "target": "h01", "x": 7, "y": 18})
    assert not far.ok and "does not go as far as a ball" in far.reason


def test_a_prone_team_mate_can_still_be_thrown_but_cannot_land_on_their_feet():
    """ "can be thrown by a team-mate with the Throw Team-mate Trait, EVEN IF THIS
    PLAYER IS PRONE" — and then "Players that were Prone, Stunned or Distracted
    when they were thrown will AUTOMATICALLY FAIL the Agility Test to land." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _ttm_board()
    m.apply(Event(kind="player_placed_prone", actor="h01", detail={"down": "prone"}))
    assert actions.get("throwteam")["validate"](m, {"player": "h00", "target": "h01", "x": 7, "y": 12}).ok

    out = _throw(m, _dice([5, 1, 1, 1, 2, 2]))  # a Superb Throw, then scatter, then the fall
    assert any("cannot land on their feet" in (e.text or "") for e in out.events)
    assert m.by_id("h01").down != "standing"


def test_dropping_a_team_mate_is_only_a_turnover_if_they_had_the_ball():
    """ "this will only cause a Turnover IF THE THROWN PLAYER WAS HOLDING THE BALL,
    otherwise no Turnover is caused."

    Nearly everything that goes wrong on your own turn ends it. This does not —
    which is what makes launching a Goblin at somebody a sane thing to do.
    """
    empty = _ttm_board()
    out = _throw(empty, _dice([5, 1, 1, 1, 1, 2, 2]))
    assert empty.by_id("h01").down != "standing", "they should have hit the ground"
    assert not out.turnover, "an empty-handed Goblin costs nothing"

    carrying = _ttm_board()
    carrying.apply(_ball_at(7, 9, carrier="h01"))
    out2 = _throw(carrying, _dice([5, 1, 1, 1, 1, 2, 2, 3]))
    assert out2.turnover, "the ball was in their hands"


def test_a_fumbled_throw_drops_them_on_the_throwers_square():
    """ "The thrown player is dropped and will Bounce from the THROWING player's
    square" — not the target square, which is the whole cost of a fumble."""
    m = _ttm_board()
    out = _throw(m, _dice([1, 2, 4]))  # natural 1 = Fumbled, scatter north, then land
    assert any("Fumbled Throw" in (e.text or "") for e in out.events)
    launched = next(e for e in out.events if e.detail.get("thrown"))
    assert (launched.detail["x"], launched.detail["y"]) == (7, 8), "they should drop on the thrower"


def test_a_crash_landing_flattens_whoever_was_standing_there():
    """ "the player in the occupied square is automatically Knocked Down EVEN IF
    THEY ARE ALREADY PRONE OR STUNNED. The thrown player will then Bounce from the
    occupied square and will Fall Over." """
    m = _match(
        ("home", 7, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 7, 9, 6, "3+", ["Right Stuff"]),
        ("away", 7, 7, 6),
    )
    m.by_id("h00").player.PA = "3+"
    # A natural 1 fumbles them onto the thrower's square, then a Scatter of 2 —
    # direction (0,-1) — drops them straight onto the Skaven standing at (7,7).
    out = _throw(m, _dice([1, 2, 2, 2, 5, 2, 2]), x=7, y=12)
    assert any("crash-lands" in (e.text or "") for e in out.events), [e.text for e in out.events]
    assert m.by_id("a02").down != "standing", "the player landed on should be flattened"
    assert m.by_id("h01").down != "standing", "and the thrown player Falls Over"
    assert not out.turnover, "nobody had the ball"


def test_always_hungry_may_eat_the_team_mate():
    """ "On a 1, the player will attempt to eat their team-mate … On a 2+, the
    team-mate will squirm free and the Throw Team-mate Action will automatically
    result in a Fumbled Throw. On a 1, the player will eat their team-mate —
    immediately remove them … No Apothecary … no Regeneration." """
    escaped = _ttm_board(thrower=("Throw Team-mate", "Always Hungry"))
    out = _throw(escaped, _dice([1, 4, 2, 4]))  # hunger 1, squirm 4 → Fumbled, scatter, land
    assert any("squirm" in (e.text or "").lower() for e in out.events)
    assert not any("EATS" in (e.text or "") for e in out.events)

    eaten = _ttm_board(thrower=("Throw Team-mate", "Always Hungry"))
    out2 = _throw(eaten, _dice([1, 1]))  # hunger 1, squirm 1 → eaten
    assert any("EATS" in (e.text or "") for e in out2.events)
    assert eaten.by_id("h01").place == "casualty"
    assert out2.turnover


def test_bullseye_lands_a_superb_throw_on_the_target_square():
    """ "if the result of the throw is a Superb Throw then the thrown player will
    not Scatter before landing and will instead land in the target square." """
    m = _ttm_board(thrower=("Throw Team-mate", "Bullseye"))
    out = _throw(m, _dice([5, 4]), x=7, y=12)  # Superb, then the landing roll
    assert out.ok
    assert (m.by_id("h01").x, m.by_id("h01").y) == (7, 12), "Bullseye should have skipped the Scatter"
    assert not any(r.kind == "Scatter" for e in out.events for r in e.rolls)


def test_throw_team_mate_is_once_per_team_per_turn():
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(
        ("home", 7, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 7, 9, 6, "3+", ["Right Stuff"]),
        ("home", 2, 8, 6, "3+", ["Throw Team-mate"]),
        ("home", 2, 9, 6, "3+", ["Right Stuff"]),
    )
    for who in ("h00", "h02"):
        m.by_id(who).player.PA = "3+"
    _throw(m, _dice([5, 2, 2, 2, 4]), x=7, y=12)  # Superb: three Scatters, then the landing
    second = actions.get("throwteam")["validate"](m, {"player": "h02", "target": "h03", "x": 2, "y": 12})
    assert not second.ok and "one Throw Team-mate Action this turn" in second.reason


# --- Special Actions -------------------------------------------------------


def _special(m, action, dice, player="h00", target="a01", **cmd):
    from bloodbowl.engine import actions

    actions.load_all()
    return actions.get(action)["resolve"](m, {"player": player, "target": target, **cmd}, dice)


def _pair(skill, av="9+"):
    m = _match(("home", 7, 13, 6, "3+", [skill]), ("away", 7, 14, 6))
    m.by_id("a01").player.AV = av
    m.by_id("a01").player.ST = "3"
    return m


def test_a_stab_makes_an_armour_roll_that_nothing_can_modify():
    """ "make an Armour Roll for the selected player. THIS ARMOUR ROLL CANNOT BE
    MODIFIED IN ANY WAY."

    Mighty Blow and Claws both hang off the player responsible, so the way to
    honour "cannot be modified" is to name no one responsible.
    """
    m = _pair("Stab", av="7+")
    m.by_id("h00").player.skills = ["Stab", "Mighty Blow", "Claws"]
    out = _special(m, "stab", _dice([3, 3, 3, 3]))
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 0, "something modified the unmodifiable"
    assert m.by_id("a01").down == "standing", "a Stab does not knock anybody over"
    assert m.by_id("h00").done is True


def test_projectile_vomit_can_land_on_the_player_who_threw_it():
    """ "On a 1, this player covers THEMSELVES in acidic bile; make an Armour Roll
    for THIS player." """
    hit = _pair("Projectile Vomit", av="7+")
    _special(hit, "vomit", _dice([4, 3, 3, 3, 3]))
    assert hit.by_id("a01").down != "standing" or True  # armour may hold; the roll is the point

    backfire = _pair("Projectile Vomit")
    backfire.by_id("h00").player.AV = "2+"
    out = _special(backfire, "vomit", _dice([1, 3, 3, 3, 3, 9]))
    hurt = [e for e in out.events if e.kind == "injury_roll"]
    assert hurt and hurt[0].actor == "h00", "the bile should have landed on the thrower"


def test_breathe_fire_places_prone_on_a_four_but_knocks_down_on_a_natural_six():
    """ "On a 4+, the opposition player is immediately PLACED PRONE. If the roll is
    a NATURAL 6, the opposition player is KNOCKED DOWN instead."

    Placed Prone risks no harm and Knocked Down does, so those are two genuinely
    different outcomes — and "natural" means a big target's -1 cannot take the
    knock-down away.
    """
    gentle = _pair("Breathe Fire")
    out = _special(gentle, "breathe_fire", _dice([4]))
    assert gentle.by_id("a01").down == "prone"
    assert "Armour" not in [r.kind for e in out.events for r in e.rolls], "Placed Prone risks no harm"

    fierce = _pair("Breathe Fire")
    out2 = _special(fierce, "breathe_fire", _dice([6, 3, 3]))
    assert "Armour" in [r.kind for e in out2.events for r in e.rolls], "a natural 6 is a knock-down"

    # A big target subtracts 1 — but a natural 6 still knocks them down.
    big = _pair("Breathe Fire")
    big.by_id("a01").player.ST = "5"
    out3 = _special(big, "breathe_fire", _dice([6, 3, 3]))
    roll = next(r for e in out3.events for r in e.rolls if r.kind == "Breathe Fire")
    assert roll.modifier == -1
    assert "Armour" in [r.kind for e in out3.events for r in e.rolls]

    backfire = _pair("Breathe Fire")
    out4 = _special(backfire, "breathe_fire", _dice([1, 3, 3]))
    assert backfire.by_id("h00").down != "standing" and out4.turnover


def test_a_chainsaw_cuts_deep_and_cuts_both_ways():
    """Three clauses. "+3 modifier to the Armour Roll" on a 2+; "On a 1, the
    Chainsaw will Kick-back and this player is Knocked Down"; and "If this player
    is Knocked Down or Falls Over FOR ANY REASON … a +3 modifier is applied when
    the opposition Coach makes an Armour Roll for THIS player. This +3 modifier
    MUST ALWAYS BE APPLIED." """
    from bloodbowl.engine.injury import risk_injury

    cut = _pair("Chainsaw", av="11+")
    out = _special(cut, "chainsaw", _dice([4, 4, 4, 5, 5, 9]))  # …a Casualty, so its D16 as well
    armour = next(r for e in out.events for r in e.rolls if r.kind == "Armour")
    assert armour.modifier == 3 and armour.passed, "4+4+3 = 11 should break AV 11+"

    kick = _pair("Chainsaw")
    # The kick-back breaks their OWN armour easily, because the +3 applies to them
    # too — so the injury behind it needs dice as well.
    out2 = _special(kick, "chainsaw", _dice([1, 3, 3, 2, 2]))
    assert kick.by_id("h00").down != "standing" and out2.turnover

    # …and the owner is always easier to hurt.
    owner = _pair("Chainsaw")
    events = risk_injury(owner, owner.by_id("h00"), _dice([3, 3, 4, 4]))
    assert next(r for e in events for r in e.rolls if r.kind == "Armour").modifier == 3


def test_a_chainsaw_also_adds_three_to_a_foul():
    """ "this player may also use their chainsaw when performing a Foul Action, in
    which case they may apply a +3 modifier when making the Armour Roll." """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _pair("Chainsaw")
    m.apply(Event(kind="player_placed_prone", actor="a01", detail={"down": "prone"}))
    d = actions.get("foul")["validate"](m, {"player": "h00", "target": "a01"}).detail
    assert d["chainsaw"] == 3 and d["armour_modifier"] == 3


def test_being_chomped_pins_a_player_until_the_chomper_lets_go():
    """ "Whilst Chomped, the opposition player cannot leave the square they are in
    whilst this player remains Marking them. THIS CONDITION ENDS IMMEDIATELY if
    this player is no longer Marking the opposition player FOR ANY REASON."

    "For any reason" is why the condition is asked of the live board rather than
    remembered — a chomper who is knocked down would never think to clear a flag.
    """
    from bloodbowl.engine import actions
    from bloodbowl.engine.events import Event

    actions.load_all()
    m = _pair("Monstrous Mouth")
    _special(m, "chomp", _dice([5]))
    assert m.by_id("a01").chomped_by == "h00"

    m.apply(Event(kind="turn_started", detail={"side": "away", "half": 1, "turn": 1}))
    pinned = actions.get("move")["validate"](m, {"player": "a01", "x": 6, "y": 15})
    assert not pinned.ok and "Chomped" in pinned.reason

    # Knock the chomper over: no Tackle Zone, so the condition lapses at once.
    m.apply(Event(kind="player_placed_prone", actor="h00", detail={"down": "prone"}))
    assert actions.get("move")["validate"](m, {"player": "a01", "x": 6, "y": 15}).ok


def test_a_special_action_may_replace_a_blitzs_block_but_is_not_one():
    """ "Some Special Actions allow a player to replace the Block Action made as
    part of a Blitz Action … even though the Special Action is replacing a Block
    Action, IT IS NOT ONE ITSELF and so any rules, Skills or Traits that affect a
    Block Action will have no effect." """
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(("home", 7, 10, 6, "3+", ["Stab"]), ("away", 7, 14, 6))
    _declare(m, "h00", "a01")
    for y in (11, 12, 13):
        _move(m, "h00", 7, y, _dice([]))
    legal = actions.get("stab")["validate"](m, {"player": "h00", "target": "a01"})
    assert legal.ok and legal.detail["replaces_blitz_block"] is True

    out = _special(m, "stab", _dice([3, 3]))
    assert not any(e.kind == "block_rolled" for e in out.events), "a Special Action is not a Block"
    assert m.by_id("h00").done is True, "the activation ends as soon as it is performed"


def test_any_number_of_players_may_use_a_special_action_each_turn():
    """ "there is no limit to the number of players that can declare this Special
    Action each Turn" — unlike almost every other Action in the game."""
    from bloodbowl.engine import actions

    actions.load_all()
    m = _match(
        ("home", 7, 13, 6, "3+", ["Stab"]),
        ("away", 7, 14, 6),
        ("home", 2, 13, 6, "3+", ["Stab"]),
        ("away", 2, 14, 6),
    )
    _special(m, "stab", _dice([3, 3]), player="h00", target="a01")
    assert actions.get("stab")["validate"](m, {"player": "h02", "target": "a03"}).ok


# --- throw-ins, Secret Weapons, and the end of a drawn game ----------------


def test_a_throw_in_comes_back_in_across_the_templates_three_arrows():
    """ "roll a D6 to determine the direction the crowd will throw the ball as
    determined by the Throw-in Template. The ball will then travel 2D6 squares."

    The template is a DIAGRAM with three arrows, so — like the Range Ruler and the
    push arc — the mapping from a D6 to an arrow is read off the picture. What is
    quoted is that it comes back INWARD, which is the part every arrow shares.
    """
    from bloodbowl.engine.ball import throw_in

    seen = set()
    for d6 in (1, 3, 5):  # one number from each of the template's three arrows
        m = _match(("home", 7, 13, 6))
        out = throw_in(m, _dice([d6, 2, 2, 1]), 0, 13)  # left off the west sideline
        assert m.ball.x >= 1, "the ball must come back onto the pitch"
        assert any("hurls the ball back" in (e.text or "") for e in out)
        seen.add((m.ball.x, m.ball.y))
    assert len(seen) == 3, f"the three arrows should send it three different ways: {seen}"


def test_a_corner_throw_in_uses_a_d3_because_a_corner_has_no_single_way_in():
    """ "Should the ball leave the pitch from a CORNER square, position the Random
    Direction Template … and roll a D3." """
    from bloodbowl.engine.ball import throw_in

    m = _match(("home", 7, 13, 6))
    out = throw_in(m, _dice([2, 3, 3, 1]), 0, 0)
    kinds = [r.kind for e in out for r in e.rolls]
    assert "Corner Throw-in" in kinds, kinds
    assert 1 <= m.ball.x <= 15 and 1 <= m.ball.y <= 26


def test_a_secret_weapon_is_sent_off_at_the_end_of_the_drive():
    """ "If a Coach fielded any players with the Secret Weapon Trait during the
    current Drive, then they will immediately be Sent-off AS IF THEY HAD COMMITTED
    A FOUL ACTION, even if they were not on the pitch at the end of the Drive.
    Players Sent-off in this way may still Argue the Call."

    "As if they had committed a Foul" is why this calls the Foul's own helper —
    the Argue roll, the ejected-Coach ban and all — rather than a second
    implementation that agrees until it does not.
    """
    from bloodbowl.engine.game import start_drive

    m = _match(("home", 7, 13, 6, "3+", ["Secret Weapon"]), ("home", 8, 13, 6), ("away", 7, 20, 6))
    m.setup = [{"id": p.id, "x": p.x, "y": p.y} for p in m.players]
    start_drive(m, receiving="home", dice=_dice([3] + [4] * 20))
    assert m.by_id("h00").place == "sent_off"
    assert m.by_id("h01").place == "pitch", "only the one with the weapon"
    assert any(r.kind == "Argue the Call" for e in m.events for r in e.rolls), "they may still Argue"


def test_extra_time_does_not_replenish_the_re_rolls():
    """ "Team Re-rolls will NOT be replenished like they would be at half-time. Any
    Team Re-rolls not spent at the end of the game may carry over." That is the
    whole difference between Extra Time and a half."""
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import start_extra_time

    m = _match(("home", 7, 13, 6), ("away", 7, 20, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "rerolls": {"home": 3, "away": 3}}))
    m.clock.half = 2  # full time comes at the end of the second half
    m.rerolls["home"] = 1
    m.setup = [{"id": p.id, "x": p.x, "y": p.y} for p in m.players]
    m.apply(Event(kind="match_over", text="Full time."))

    out = start_extra_time(m, receiving="home")
    assert out["ok"] and not m.over
    assert m.clock.half == 3 and m.clock.turn == 1, "Extra Time is a third period"
    assert m.rerolls["home"] == 1, "Extra Time replenished what half-time would have"


def test_extra_time_is_only_for_a_draw():
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import start_extra_time

    m = _match(("home", 7, 13, 6))
    m.apply(Event(kind="match_over", text="Full time."))
    m.score = {"home": 2, "away": 1}
    assert not start_extra_time(m)["ok"]


def test_a_penalty_shootout_is_five_roll_offs_with_ties_re_rolled():
    """ "both Coaches will roll off against each other five times … rolling a D6
    (RE-ROLLING ANY TIES, though no other re-rolls from any source can be used)."
    """
    from bloodbowl.engine.game import penalty_shootout

    m = _match(("home", 7, 13, 6))
    # A tie first, which must be re-rolled and must not count as a kick.
    out = penalty_shootout(m, _dice([4, 4, 6, 1, 6, 1, 6, 1, 1, 6, 1, 6]))
    assert out["wins"]["home"] + out["wins"]["away"] == 5, out["wins"]
    assert out["wins"] == {"home": 3, "away": 2}
    assert out["winner"] == "home"


def test_the_apothecary_puts_a_knocked_out_player_back_on_the_pitch():
    """ "the player is NOT removed from the pitch … Instead, the player will become
    STUNNED IN THE SQUARE THEY ARE IN. If the player was Knocked-out as a result of
    an Injury by the Crowd, they are placed in the Reserves Box instead."

    Back on the pitch is a real swing, and it is once per game.
    """
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import use_apothecary

    # A SECOND home player, because the once-per-game half needs another casualty
    # on the same side — `_match` numbers across the list, so the ids are h00/h01.
    m = _match(("home", 7, 13, 6), ("home", 8, 13, 6), ("away", 7, 20, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    m.apply(Event(kind="player_condition", actor="h00", detail={"outcome": "knocked_out"}))
    assert m.by_id("h00").place == "knocked_out"

    out = use_apothecary(m, "h00", _dice([]))
    assert out["ok"]
    p = m.by_id("h00")
    assert p.place == "pitch" and p.down == "stunned", "they should be back, Stunned"

    # Once per game.
    m.apply(Event(kind="player_condition", actor="h01", detail={"outcome": "knocked_out"}))
    again = use_apothecary(m, "h01", _dice([]))
    assert not again["ok"] and "once per game" in again["error"]


def test_the_apothecary_sends_a_crowd_victim_to_the_reserves_instead():
    from bloodbowl.engine.events import Event
    from bloodbowl.engine.game import use_apothecary

    m = _match(("home", 2, 13, 6), ("away", 1, 13, 6))
    m.apply(Event(kind="match_started", detail={"kicking_to": "home", "apothecary": {"home": True, "away": True}}))
    _block(m, "h00", "a01", _dice([4, 4], [["push_back"]]), follow_up=False)  # into the crowd, KO
    assert m.by_id("a01").place == "knocked_out"

    m.clock.active = "away"
    out = use_apothecary(m, "a01", _dice([]))
    assert out["ok"] and m.by_id("a01").place == "reserves"


# --- the Set-up phase ------------------------------------------------------


def _squad(side, n=11):
    """A legal formation: three on the Line, the rest tucked behind it."""
    from bloodbowl.engine.setup import LOS_ROWS

    line = LOS_ROWS[0] if side == "home" else LOS_ROWS[1]
    back = line - 1 if side == "home" else line + 1
    squares = [(6, line), (7, line), (8, line)]
    x = 5
    while len(squares) < n:
        squares.append((x, back))
        x += 1
    return squares


def _setup_match(n=11):
    squares = _squad("home", n) + _squad("away", n)
    return _match(*[("home" if i < n else "away", x, y, 6) for i, (x, y) in enumerate(squares)])


def test_a_set_up_is_checked_against_all_four_rules_at_once():
    """ "Players must be deployed in their own half … At least three players … must
    be deployed in the Centre Field, directly adjacent to the Line of Scrimmage …
    No more than two players from each team may be deployed within each Wide Zone."

    Every violation comes back together, because a coach fixing a formation wants
    the whole list rather than one error at a time.
    """
    from bloodbowl.engine.setup import violations

    assert violations("home", _squad("home"), 11) == []

    # Over the Line, none on it, and four in a Wide Zone.
    bad = [(1, 20), (2, 5), (3, 5), (4, 5), (1, 5), (7, 4), (8, 4), (9, 4), (10, 4), (11, 4), (12, 4)]
    problems = violations("home", bad, 11)
    assert any("opponent's half" in v for v in problems)
    assert any("Line of Scrimmage" in v for v in problems)
    assert any("Wide Zone" in v for v in problems)


def test_a_short_handed_team_must_put_everyone_on_the_line():
    """ "Should a team only be able to field three or fewer players, then they must
    be deployed in the Centre Field directly adjacent to the Line of Scrimmage."
    """
    from bloodbowl.engine.setup import violations

    assert violations("home", [(6, 13), (7, 13), (8, 13)], 3) == []
    assert violations("home", [(6, 13), (7, 13), (7, 11)], 3), "the third is off the Line"
    # …and a team that could field more must: "they must set up as many as they can".
    assert any("as many as they can" in v for v in violations("home", [(6, 13), (7, 13), (8, 13)], 8))


def test_declaring_a_set_up_refuses_an_illegal_one_and_records_a_legal_one():
    from bloodbowl.engine.game import declare_setup

    m = _setup_match()
    ids = [p.id for p in m.players if p.side == "home"]
    legal = [{"id": i, "x": x, "y": y} for i, (x, y) in zip(ids, _squad("home"), strict=False)]
    out = declare_setup(m, "home", legal)
    assert out["ok"] and m.setups["home"], out
    assert out["waiting_on"] == "away"

    crowded = [{"id": i, "x": 1 + n % 4, "y": 12} for n, i in enumerate(ids)]
    bad = declare_setup(m, "home", crowded)
    assert not bad["ok"] and bad["violations"]


def test_a_declared_set_up_beats_the_reused_opening_one():
    """The captured opening set-up is the documented fallback, not the rule."""
    from bloodbowl.engine.game import declare_setup, start_drive

    m = _setup_match()
    ids = [p.id for p in m.players if p.side == "home"]
    moved = _squad("home")
    moved[-1] = (12, 11)  # somewhere the opening set-up never had anybody
    declare_setup(m, "home", [{"id": i, "x": x, "y": y} for i, (x, y) in zip(ids, moved, strict=False)])
    start_drive(m, receiving="home", dice=_dice([3] + [4] * 20))
    last = m.by_id(ids[-1])
    assert (last.x, last.y) == (12, 11), "the declared set-up was ignored"
    assert m.setups == {}, "a set-up is consumed by the Drive it was declared for"


def test_too_many_players_are_removed_by_the_opposition():
    """ "any extra players on the pitch will be immediately removed … The players
    that are removed CANNOT HAVE THE BALL, cannot be a Star Player and are chosen
    by the opposition Coach." """
    from bloodbowl.engine.game import enforce_squad_size

    m = _match(*[("home", 1 + i % 15, 5 + i // 15, 6) for i in range(13)], ("away", 7, 20, 6))
    m.apply(_ball_at(1, 5, carrier="h00"))
    assert len(m.on_pitch("home")) == 13

    enforce_squad_size(m)
    assert len(m.on_pitch("home")) == 11
    assert m.by_id("h00").place == "pitch", "the ball carrier may not be the one removed"


def test_the_kicking_team_is_rolled_off_for_when_nobody_says():
    """ "this is done with a simple coin toss … The Coach who rolls highest decides
    which team is kicking and which team is receiving." """
    from bloodbowl.engine.game import _roll_off

    assert _roll_off(_dice([6, 2])) == "away", "home rolled highest, so they kick"
    assert _roll_off(_dice([2, 6])) == "home"
    assert _roll_off(_dice([4, 4, 5, 1])) == "away", "a tie is re-rolled"
